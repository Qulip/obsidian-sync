"""Regression test for the concurrent lost-update race on same-path PUTs.

Uses a real uvicorn subprocess so that N genuinely parallel HTTP connections
hit the write path at once. An in-process TestClient is single-threaded and
cannot reproduce the race, so it is deliberately not used here.
"""

import asyncio
import hashlib
import os
import socket
import subprocess
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import asyncpg
import httpx
import pytest

from obsidian_sync.domain.hashing import sha256_text
from obsidian_sync.sync_agent.client import encode_vault_path

_REPO_ROOT = Path(__file__).resolve().parent.parent
TEST_ASYNC_URL = os.environ['OBSIDIAN_SYNC_DATABASE_URL']
TEST_DSN = TEST_ASYNC_URL.replace('postgresql+asyncpg://', 'postgresql://', 1)

VAULT_ID = 'ctest'
RAW_TOKEN = 'concurrency-token'
CONCURRENCY = 8


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
                'concurrency',
                hashlib.sha256(RAW_TOKEN.encode('utf-8')).hexdigest(),
            )
        finally:
            await conn.close()

    asyncio.run(run())


@pytest.fixture
def live_server(clean_db: None, tmp_path: Path) -> Iterator[str]:
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


def _files_url(base_url: str, path: str) -> str:
    return f'{base_url}/vaults/{VAULT_ID}/files/{encode_vault_path(path)}'


def _put(base_url: str, path: str, *, base_revision: int, content: str) -> int:
    response = httpx.put(
        _files_url(base_url, path),
        json={
            'device_id': 'dev',
            'base_revision': base_revision,
            'content_hash': sha256_text(content),
            'content': content,
        },
        headers={'Authorization': f'Bearer {RAW_TOKEN}'},
        timeout=15,
    )
    return response.status_code


def test_concurrent_same_base_puts_yield_single_winner(live_server: str) -> None:
    _mint_token()
    create = httpx.post(
        f'{live_server}/vaults',
        json={'vault_id': VAULT_ID, 'name': 'Concurrency'},
        headers={'Authorization': f'Bearer {RAW_TOKEN}'},
        timeout=5,
    )
    assert create.status_code == 200, create.text

    assert _put(live_server, 'race.md', base_revision=0, content='v1') == 200

    barrier = threading.Barrier(CONCURRENCY)

    def attempt(index: int) -> tuple[str, int]:
        content = f'winner-{index}'
        barrier.wait()
        return content, _put(live_server, 'race.md', base_revision=1, content=content)

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        results = list(pool.map(attempt, range(CONCURRENCY)))

    statuses = [status for _, status in results]
    assert statuses.count(200) == 1, statuses
    assert statuses.count(409) == CONCURRENCY - 1, statuses

    winner = next(content for content, status in results if status == 200)
    served = httpx.get(
        _files_url(live_server, 'race.md'),
        headers={'Authorization': f'Bearer {RAW_TOKEN}'},
        timeout=5,
    ).json()['data']
    assert served['content'] == winner
    assert served['revision'] == 2
