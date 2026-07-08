import asyncio
import hashlib
import logging
import os
import socket
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import asyncpg
import httpx
import pytest

from obsidian_sync.domain.sync_rules import is_conflict_file
from obsidian_sync.sync_agent.client import encode_vault_path
from obsidian_sync.sync_agent.config import AgentConfig, ObsidianConfig
from obsidian_sync.sync_agent.engine import SyncSummary, run_sync

_REPO_ROOT = Path(__file__).resolve().parent.parent
TEST_ASYNC_URL = os.environ['OBSIDIAN_SYNC_DATABASE_URL']
TEST_DSN = TEST_ASYNC_URL.replace('postgresql+asyncpg://', 'postgresql://', 1)

VAULT_ID = 'itest'
RAW_TOKEN = 'integration-token'
_LOGGER = logging.getLogger('test.integration')


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return int(sock.getsockname()[1])


def _mint_token() -> None:
    async def run() -> None:
        conn = await asyncpg.connect(dsn=TEST_DSN)
        try:
            await conn.execute(
                'INSERT INTO obsidian.api_tokens (name, token_hash) VALUES ($1, $2)',
                'integration',
                hashlib.sha256(RAW_TOKEN.encode('utf-8')).hexdigest(),
            )
        finally:
            await conn.close()

    asyncio.run(run())


@pytest.fixture
def live_server(tmp_path: Path) -> Iterator[str]:
    port = _free_port()
    storage = tmp_path / 'server-storage'
    log_path = tmp_path / 'server.log'
    env = os.environ.copy()
    env['OBSIDIAN_SYNC_DATABASE_URL'] = TEST_ASYNC_URL
    env['OBSIDIAN_SYNC_VAULT_STORAGE_ROOT'] = str(storage / 'vaults')
    env['OBSIDIAN_SYNC_VAULT_ARCHIVE_ROOT'] = str(storage / 'archives')
    env['OBSIDIAN_SYNC_API_TOKEN'] = 'admin-secret'

    base_url = f'http://127.0.0.1:{port}'
    with log_path.open('wb') as log_file:
        proc = subprocess.Popen(
            [
                'uv',
                'run',
                'uvicorn',
                'obsidian_sync.app:app',
                '--host',
                '127.0.0.1',
                '--port',
                str(port),
            ],
            cwd=str(_REPO_ROOT),
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
        try:
            _wait_until_ready(base_url, proc, log_path)
            yield base_url
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)


def _wait_until_ready(base_url: str, proc: subprocess.Popen[bytes], log: Path) -> None:
    headers = {'Authorization': 'Bearer admin-secret'}
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f'server exited early:\n{log.read_text()}')
        try:
            response = httpx.get(f'{base_url}/health', headers=headers, timeout=2)
            if response.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.3)
    raise RuntimeError(f'server did not become ready:\n{log.read_text()}')


def _make_config(root: Path, base_url: str, device_id: str) -> AgentConfig:
    return AgentConfig(
        server_base_url=base_url,
        vault_id=VAULT_ID,
        vault_root=root,
        device_id=device_id,
        api_token=RAW_TOKEN,
        obsidian=ObsidianConfig(enabled=False),
    )


def _sync(root: Path, base_url: str, device_id: str) -> SyncSummary:
    summary = run_sync(
        _make_config(root, base_url, device_id), dry_run=False, logger=_LOGGER
    )
    assert summary.obsidian_ok is True
    assert summary.obsidian_message == 'obsidian integration disabled'
    return summary


def _server_get(base_url: str, path: str) -> httpx.Response:
    return httpx.get(
        f'{base_url}/vaults/{VAULT_ID}/files/{encode_vault_path(path)}',
        headers={'Authorization': f'Bearer {RAW_TOKEN}'},
        timeout=5,
    )


def _write(root: Path, rel: str, content: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')


def _conflict_files(root: Path) -> list[Path]:
    return [
        path
        for path in root.rglob('*.md')
        if is_conflict_file(path.relative_to(root).as_posix())
    ]


def test_two_vault_sync_cycle(live_server: str, tmp_path: Path) -> None:
    _mint_token()
    create = httpx.post(
        f'{live_server}/vaults',
        json={'vault_id': VAULT_ID, 'name': 'Integration'},
        headers={'Authorization': f'Bearer {RAW_TOKEN}'},
        timeout=5,
    )
    assert create.status_code == 200, create.text

    vault_a = tmp_path / 'vault-a'
    vault_b = tmp_path / 'vault-b'
    vault_a.mkdir()
    vault_b.mkdir()

    # 1. A pushes a new file, B pulls it.
    _write(vault_a, 'JPA.md', 'v1 from A')
    _sync(vault_a, live_server, 'deva')
    _sync(vault_b, live_server, 'devb')
    assert (vault_b / 'JPA.md').read_text(encoding='utf-8') == 'v1 from A'

    # 2. Both modify; A wins, B gets a conflict file with both versions.
    _write(vault_a, 'JPA.md', 'v2 from A')
    _sync(vault_a, live_server, 'deva')
    _write(vault_b, 'JPA.md', 'v2 from B')
    b_summary = _sync(vault_b, live_server, 'devb')
    assert b_summary.conflicts

    conflicts = _conflict_files(vault_b)
    assert conflicts, 'expected a .conflict.*.md file in vault B'
    conflict_body = conflicts[0].read_text(encoding='utf-8')
    assert 'v2 from A' in conflict_body
    assert 'v2 from B' in conflict_body
    assert _server_get(live_server, 'JPA.md').json()['data']['content'] == 'v2 from A'

    # 3. The conflict file is never uploaded to the server, and a second run
    # with unchanged divergence does not create additional conflict files.
    conflicts_before = _conflict_files(vault_b)
    _sync(vault_b, live_server, 'devb')
    assert len(_conflict_files(vault_b)) == len(conflicts_before)
    for conflict in _conflict_files(vault_b):
        rel = conflict.relative_to(vault_b).as_posix()
        assert _server_get(live_server, rel).status_code == 404
    changes = httpx.get(
        f'{live_server}/vaults/{VAULT_ID}/sync/changes?since=0',
        headers={'Authorization': f'Bearer {RAW_TOKEN}'},
        timeout=5,
    ).json()['data']['changes']
    assert not any('.conflict.' in change['path'] for change in changes)

    # 4. Delete in A propagates to B and soft-deletes on the server.
    _write(vault_a, 'note2.md', 'note two')
    _sync(vault_a, live_server, 'deva')
    _sync(vault_b, live_server, 'devb')
    assert (vault_b / 'note2.md').read_text(encoding='utf-8') == 'note two'

    (vault_a / 'note2.md').unlink()
    _sync(vault_a, live_server, 'deva')
    _sync(vault_b, live_server, 'devb')
    assert not (vault_b / 'note2.md').exists()
    assert _server_get(live_server, 'note2.md').status_code == 404
    deleted_rows = _fetch_deleted('note2.md')
    assert deleted_rows and deleted_rows[0] is True

    # 5. Obsidian disabled: a converged CLI sync exits 0.
    result = subprocess.run(
        [
            'uv',
            'run',
            'obsidian-sync-agent',
            'sync',
            '--server',
            live_server,
            '--vault-id',
            VAULT_ID,
            '--vault-root',
            str(vault_a),
            '--device-id',
            'deva',
        ],
        cwd=str(_REPO_ROOT),
        env={**os.environ, 'OBSIDIAN_SYNC_AGENT_TOKEN': RAW_TOKEN},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr


def _fetch_deleted(source_path: str) -> list[bool]:
    async def run() -> list[bool]:
        conn = await asyncpg.connect(dsn=TEST_DSN)
        try:
            rows = await conn.fetch(
                'SELECT deleted FROM obsidian.vault_files '
                'WHERE vault_id = $1 AND source_path = $2',
                VAULT_ID,
                source_path,
            )
            return [row['deleted'] for row in rows]
        finally:
            await conn.close()

    return asyncio.run(run())
