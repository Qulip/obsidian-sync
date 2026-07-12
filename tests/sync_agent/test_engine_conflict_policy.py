"""Engine-level tests for the conflict_policy-driven auto-resolution.

These exercise `engine._apply_write` / `engine._apply_delete` (pull-side
conflicts) and `engine._push_upsert` (push-side conflicts) directly against a
`SyncClient` backed by `httpx.MockTransport`, so no real server or database
is needed -- matching the style already used in `test_client.py`.
"""

import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest import TestCase

import httpx

from obsidian_sync.domain.hashing import sha256_text
from obsidian_sync.domain.sync_rules import is_conflict_file
from obsidian_sync.schemas.sync import SyncChangeItem
from obsidian_sync.sync_agent import engine
from obsidian_sync.sync_agent.client import SyncClient
from obsidian_sync.sync_agent.config import AgentConfig, ConflictPolicy
from obsidian_sync.sync_agent.manifest import Manifest, ManifestEntry

BASE_URL = 'https://sync.example'
DEVICE_ID = 'laptop'
VAULT_ID = 'v1'
PATH = 'notes/JPA.md'
_LOGGER = logging.getLogger('test.engine.conflict_policy')


def _make_client(handler: Callable[[httpx.Request], httpx.Response]) -> SyncClient:
    return SyncClient(
        BASE_URL,
        None,
        sleep=lambda _delay: None,
        transport=httpx.MockTransport(handler),
    )


def _config(root: Path, policy: ConflictPolicy) -> AgentConfig:
    return AgentConfig(
        server_base_url=BASE_URL,
        vault_id=VAULT_ID,
        vault_root=root,
        device_id=DEVICE_ID,
        conflict_policy=policy,
    )


def _manifest_with_entry(*, server_revision: int, content_hash: str) -> Manifest:
    manifest = Manifest(vault_id=VAULT_ID, device_id=DEVICE_ID)
    manifest.files[PATH] = ManifestEntry(
        server_revision=server_revision,
        content_hash=content_hash,
        last_synced_at='2026-07-01T00:00:00+00:00',
    )
    return manifest


def _write_local_file(root: Path, content: str) -> Path:
    destination = root / PATH
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(content, encoding='utf-8')
    return destination


def _success_response(data: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json={'success': True, 'data': data})


def _conflict_response(server_revision: int) -> httpx.Response:
    return httpx.Response(
        409,
        json={
            'success': False,
            'error': {
                'code': 'SYNC_CONFLICT',
                'message': 'revision mismatch',
                'details': {
                    'server_revision': server_revision,
                    'client_base_revision': 0,
                },
            },
        },
    )


def _backup_files(root: Path) -> list[Path]:
    return [
        path
        for path in root.rglob('*.md')
        if 'local-backup' in path.name and is_conflict_file(path.name)
    ]


def _conflict_files(root: Path) -> list[Path]:
    return [
        path
        for path in root.rglob('*.md')
        if is_conflict_file(path.name) and 'local-backup' not in path.name
    ]


class PullWriteConflictPolicyTests(TestCase):
    """Server pushed new content while the local file also diverged."""

    def _change(self, revision: int, content_hash: str) -> SyncChangeItem:
        return SyncChangeItem(
            revision=revision,
            path=PATH,
            event_type='UPDATE',
            content_hash=content_hash,
            deleted=False,
            created_at=datetime.now(UTC),
        )

    def test_manual_policy_writes_conflict_file_and_leaves_local_untouched(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            _write_local_file(root, 'LOCAL DIRTY CONTENT')
            manifest = _manifest_with_entry(
                server_revision=4, content_hash=sha256_text('OLD SYNCED CONTENT')
            )
            server_hash = sha256_text('SERVER CONTENT')

            def handler(request: httpx.Request) -> httpx.Response:
                self.assertEqual(request.method, 'GET')
                return _success_response(
                    {
                        'vault_id': VAULT_ID,
                        'path': PATH,
                        'revision': 5,
                        'content_hash': server_hash,
                        'content': 'SERVER CONTENT',
                        'deleted': False,
                    }
                )

            config = _config(root, 'manual')
            summary = engine.SyncSummary()
            with _make_client(handler) as client:
                engine._apply_write(
                    config,
                    manifest,
                    client,
                    summary,
                    _LOGGER,
                    self._change(5, server_hash),
                    root / PATH,
                    manifest.files[PATH],
                )

            self.assertEqual(summary.conflicts, [PATH])
            self.assertEqual((root / PATH).read_text(), 'LOCAL DIRTY CONTENT')
            self.assertIn(PATH, manifest.conflicts)
            conflicts = _conflict_files(root)
            self.assertEqual(len(conflicts), 1)
            body = conflicts[0].read_text(encoding='utf-8')
            self.assertIn('LOCAL DIRTY CONTENT', body)
            self.assertIn('SERVER CONTENT', body)

    def test_local_wins_pushes_local_content_and_keeps_local_file(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            _write_local_file(root, 'LOCAL DIRTY CONTENT')
            manifest = _manifest_with_entry(
                server_revision=4, content_hash=sha256_text('OLD SYNCED CONTENT')
            )
            server_hash = sha256_text('SERVER CONTENT')
            calls: list[httpx.Request] = []

            def handler(request: httpx.Request) -> httpx.Response:
                calls.append(request)
                if request.method == 'GET':
                    return _success_response(
                        {
                            'vault_id': VAULT_ID,
                            'path': PATH,
                            'revision': 5,
                            'content_hash': server_hash,
                            'content': 'SERVER CONTENT',
                            'deleted': False,
                        }
                    )
                # local-wins PUT: pushes local content as the new revision.
                self.assertEqual(request.method, 'PUT')
                body = json.loads(request.content)
                self.assertEqual(body['base_revision'], 5)
                return _success_response(
                    {
                        'vault_id': VAULT_ID,
                        'path': PATH,
                        'revision': 6,
                        'content_hash': sha256_text('LOCAL DIRTY CONTENT'),
                    }
                )

            config = _config(root, 'local-wins')
            summary = engine.SyncSummary()
            with _make_client(handler) as client:
                engine._apply_write(
                    config,
                    manifest,
                    client,
                    summary,
                    _LOGGER,
                    self._change(5, server_hash),
                    root / PATH,
                    manifest.files[PATH],
                )

            self.assertEqual(summary.conflicts, [])
            self.assertEqual(summary.pushed, 1)
            self.assertEqual((root / PATH).read_text(), 'LOCAL DIRTY CONTENT')
            self.assertNotIn(PATH, manifest.conflicts)
            self.assertEqual(manifest.files[PATH].server_revision, 6)
            self.assertEqual(_conflict_files(root), [])
            self.assertEqual(_backup_files(root), [])

    def test_local_wins_falls_back_to_manual_after_exhausting_retries(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            _write_local_file(root, 'LOCAL DIRTY CONTENT')
            manifest = _manifest_with_entry(
                server_revision=4, content_hash=sha256_text('OLD SYNCED CONTENT')
            )
            server_hash = sha256_text('SERVER CONTENT')
            put_calls = 0

            def handler(request: httpx.Request) -> httpx.Response:
                nonlocal put_calls
                if request.method == 'GET':
                    return _success_response(
                        {
                            'vault_id': VAULT_ID,
                            'path': PATH,
                            'revision': 5,
                            'content_hash': server_hash,
                            'content': 'SERVER CONTENT',
                            'deleted': False,
                        }
                    )
                put_calls += 1
                # Every retry hits another conflict (the path keeps changing
                # underneath us), so local-wins must eventually give up.
                return _conflict_response(server_revision=5 + put_calls)

            config = _config(root, 'local-wins')
            summary = engine.SyncSummary()
            with _make_client(handler) as client:
                engine._apply_write(
                    config,
                    manifest,
                    client,
                    summary,
                    _LOGGER,
                    self._change(5, server_hash),
                    root / PATH,
                    manifest.files[PATH],
                )

            self.assertEqual(summary.conflicts, [PATH])
            self.assertEqual((root / PATH).read_text(), 'LOCAL DIRTY CONTENT')
            self.assertEqual(len(_conflict_files(root)), 1)
            # Bounded retries: it must not have looped forever.
            self.assertLessEqual(put_calls, 2)

    def test_remote_wins_backs_up_local_and_adopts_server_content(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            _write_local_file(root, 'LOCAL DIRTY CONTENT')
            manifest = _manifest_with_entry(
                server_revision=4, content_hash=sha256_text('OLD SYNCED CONTENT')
            )
            server_hash = sha256_text('SERVER CONTENT')

            def handler(request: httpx.Request) -> httpx.Response:
                self.assertEqual(request.method, 'GET')
                return _success_response(
                    {
                        'vault_id': VAULT_ID,
                        'path': PATH,
                        'revision': 5,
                        'content_hash': server_hash,
                        'content': 'SERVER CONTENT',
                        'deleted': False,
                    }
                )

            config = _config(root, 'remote-wins')
            summary = engine.SyncSummary()
            with _make_client(handler) as client:
                engine._apply_write(
                    config,
                    manifest,
                    client,
                    summary,
                    _LOGGER,
                    self._change(5, server_hash),
                    root / PATH,
                    manifest.files[PATH],
                )

            self.assertEqual(summary.conflicts, [])
            self.assertEqual(summary.applied, 1)
            self.assertEqual((root / PATH).read_text(), 'SERVER CONTENT')
            self.assertEqual(manifest.files[PATH].server_revision, 5)
            self.assertNotIn(PATH, manifest.conflicts)
            backups = _backup_files(root)
            self.assertEqual(len(backups), 1)
            body = backups[0].read_text(encoding='utf-8')
            self.assertIn('remote-wins', body)
            self.assertIn('LOCAL DIRTY CONTENT', body)
            self.assertEqual(_conflict_files(root), [])


class PushUpsertConflictPolicyTests(TestCase):
    """Our own PUT was rejected with 409 because the server changed too."""

    def test_local_wins_resolves_via_retry(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            _write_local_file(root, 'LOCAL NEW CONTENT')
            manifest = _manifest_with_entry(
                server_revision=4, content_hash=sha256_text('LOCAL NEW CONTENT')
            )
            put_calls = 0

            def handler(request: httpx.Request) -> httpx.Response:
                nonlocal put_calls
                self.assertEqual(request.method, 'PUT')
                put_calls += 1
                if put_calls == 1:
                    return _conflict_response(server_revision=7)
                return _success_response(
                    {
                        'vault_id': VAULT_ID,
                        'path': PATH,
                        'revision': 8,
                        'content_hash': sha256_text('LOCAL NEW CONTENT'),
                    }
                )

            config = _config(root, 'local-wins')
            summary = engine.SyncSummary()
            with _make_client(handler) as client:
                engine._push_upsert(
                    config, manifest, client, summary, _LOGGER, PATH, base_revision=4
                )

            self.assertEqual(summary.conflicts, [])
            self.assertEqual(summary.pushed, 1)
            self.assertEqual(manifest.files[PATH].server_revision, 8)
            self.assertEqual(_conflict_files(root), [])
            self.assertEqual(put_calls, 2)

    def test_remote_wins_adopts_server_content_and_backs_up_local(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            _write_local_file(root, 'LOCAL NEW CONTENT')
            manifest = _manifest_with_entry(
                server_revision=4, content_hash=sha256_text('LOCAL NEW CONTENT')
            )
            server_hash = sha256_text('SERVER CONTENT')

            def handler(request: httpx.Request) -> httpx.Response:
                if request.method == 'PUT':
                    return _conflict_response(server_revision=7)
                self.assertEqual(request.method, 'GET')
                return _success_response(
                    {
                        'vault_id': VAULT_ID,
                        'path': PATH,
                        'revision': 7,
                        'content_hash': server_hash,
                        'content': 'SERVER CONTENT',
                        'deleted': False,
                    }
                )

            config = _config(root, 'remote-wins')
            summary = engine.SyncSummary()
            with _make_client(handler) as client:
                engine._push_upsert(
                    config, manifest, client, summary, _LOGGER, PATH, base_revision=4
                )

            self.assertEqual(summary.conflicts, [])
            self.assertEqual(summary.applied, 1)
            self.assertEqual((root / PATH).read_text(), 'SERVER CONTENT')
            self.assertEqual(manifest.files[PATH].server_revision, 7)
            backups = _backup_files(root)
            self.assertEqual(len(backups), 1)
            self.assertIn('LOCAL NEW CONTENT', backups[0].read_text(encoding='utf-8'))


class PullDeleteConflictPolicyTests(TestCase):
    """Server deleted the file while the local copy also diverged."""

    def _change(self, revision: int) -> SyncChangeItem:
        return SyncChangeItem(
            revision=revision,
            path=PATH,
            event_type='DELETE',
            content_hash=None,
            deleted=True,
            created_at=datetime.now(UTC),
        )

    def test_local_wins_recreates_file_on_server(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            _write_local_file(root, 'LOCAL DIRTY CONTENT')
            manifest = _manifest_with_entry(
                server_revision=4, content_hash=sha256_text('OLD SYNCED CONTENT')
            )

            def handler(request: httpx.Request) -> httpx.Response:
                self.assertEqual(request.method, 'PUT')
                body = json.loads(request.content)
                # Recreating a server-deleted file always uses base_revision=0.
                self.assertEqual(body['base_revision'], 0)
                return _success_response(
                    {
                        'vault_id': VAULT_ID,
                        'path': PATH,
                        'revision': 6,
                        'content_hash': sha256_text('LOCAL DIRTY CONTENT'),
                    }
                )

            config = _config(root, 'local-wins')
            summary = engine.SyncSummary()
            with _make_client(handler) as client:
                engine._apply_delete(
                    config,
                    manifest,
                    client,
                    summary,
                    _LOGGER,
                    self._change(5),
                    root / PATH,
                    manifest.files[PATH],
                )

            self.assertEqual(summary.conflicts, [])
            self.assertEqual(summary.pushed, 1)
            self.assertEqual((root / PATH).read_text(), 'LOCAL DIRTY CONTENT')
            self.assertEqual(manifest.files[PATH].server_revision, 6)
            self.assertEqual(_conflict_files(root), [])

    def test_remote_wins_backs_up_and_deletes_locally(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            _write_local_file(root, 'LOCAL DIRTY CONTENT')
            manifest = _manifest_with_entry(
                server_revision=4, content_hash=sha256_text('OLD SYNCED CONTENT')
            )

            def handler(request: httpx.Request) -> httpx.Response:
                self.fail(f'unexpected request: {request.method} {request.url}')

            config = _config(root, 'remote-wins')
            summary = engine.SyncSummary()
            with _make_client(handler) as client:
                engine._apply_delete(
                    config,
                    manifest,
                    client,
                    summary,
                    _LOGGER,
                    self._change(5),
                    root / PATH,
                    manifest.files[PATH],
                )

            self.assertEqual(summary.conflicts, [])
            self.assertEqual(summary.locally_deleted, 1)
            self.assertFalse((root / PATH).exists())
            self.assertNotIn(PATH, manifest.files)
            backups = _backup_files(root)
            self.assertEqual(len(backups), 1)
            self.assertIn('LOCAL DIRTY CONTENT', backups[0].read_text(encoding='utf-8'))
