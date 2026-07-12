"""Engine-level tests for attachment (non-markdown) push/pull/conflict.

Mirrors the style of `test_engine_conflict_policy.py` (mocked `SyncClient`,
no real server), scoped to attachment-specific wire format (base64 +
`encoding`) and the extension-preserving conflict file naming that markdown
paths do not exercise.
"""

import base64
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest import TestCase

import httpx

from obsidian_sync.domain.hashing import sha256_bytes
from obsidian_sync.domain.sync_rules import is_conflict_file
from obsidian_sync.schemas.sync import SyncChangeItem
from obsidian_sync.sync_agent import engine
from obsidian_sync.sync_agent.client import SyncClient
from obsidian_sync.sync_agent.config import AgentConfig
from obsidian_sync.sync_agent.manifest import Manifest, ManifestEntry

BASE_URL = 'https://sync.example'
DEVICE_ID = 'laptop'
VAULT_ID = 'v1'
PATH = 'attachments/photo.png'
RAW = b'\x89PNG\r\n\x1a\nbinary-bytes'
_LOGGER = logging.getLogger('test.engine.attachments')


def _make_client(handler: Callable[[httpx.Request], httpx.Response]) -> SyncClient:
    return SyncClient(
        BASE_URL,
        None,
        sleep=lambda _delay: None,
        transport=httpx.MockTransport(handler),
    )


def _config(root: Path) -> AgentConfig:
    return AgentConfig(
        server_base_url=BASE_URL,
        vault_id=VAULT_ID,
        vault_root=root,
        device_id=DEVICE_ID,
        sync_attachments=True,
    )


def _success_response(data: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json={'success': True, 'data': data})


class PushUpsertAttachmentTests(TestCase):
    def test_push_upsert_sends_base64_and_records_manifest(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            destination = root / PATH
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(RAW)
            manifest = Manifest(vault_id=VAULT_ID, device_id=DEVICE_ID)
            seen: dict[str, Any] = {}

            def handler(request: httpx.Request) -> httpx.Response:
                seen.update(json.loads(request.content))
                return _success_response(
                    {
                        'vault_id': VAULT_ID,
                        'path': PATH,
                        'revision': 1,
                        'content_hash': sha256_bytes(RAW),
                    }
                )

            config = _config(root)
            summary = engine.SyncSummary()
            with _make_client(handler) as client:
                engine._push_upsert(
                    config, manifest, client, summary, _LOGGER, PATH, base_revision=0
                )

            self.assertEqual(seen['encoding'], 'base64')
            self.assertEqual(base64.b64decode(seen['content']), RAW)
            self.assertEqual(seen['content_hash'], sha256_bytes(RAW))
            self.assertEqual(summary.pushed, 1)
            self.assertEqual(manifest.files[PATH].server_revision, 1)


class ApplyWriteAttachmentTests(TestCase):
    def _change(self, revision: int, content_hash: str) -> SyncChangeItem:
        return SyncChangeItem(
            revision=revision,
            path=PATH,
            event_type='CREATE',
            content_hash=content_hash,
            deleted=False,
            created_at=datetime.now(UTC),
        )

    def test_new_attachment_is_written_as_raw_bytes(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            manifest = Manifest(vault_id=VAULT_ID, device_id=DEVICE_ID)
            content_hash = sha256_bytes(RAW)

            def handler(request: httpx.Request) -> httpx.Response:
                self.assertEqual(request.method, 'GET')
                return _success_response(
                    {
                        'vault_id': VAULT_ID,
                        'path': PATH,
                        'revision': 1,
                        'content_hash': content_hash,
                        'content': base64.b64encode(RAW).decode('ascii'),
                        'encoding': 'base64',
                        'deleted': False,
                    }
                )

            config = _config(root)
            summary = engine.SyncSummary()
            with _make_client(handler) as client:
                engine._apply_write(
                    config,
                    manifest,
                    client,
                    summary,
                    _LOGGER,
                    self._change(1, content_hash),
                    root / PATH,
                    None,
                )

            self.assertEqual((root / PATH).read_bytes(), RAW)
            self.assertEqual(summary.applied, 1)
            self.assertEqual(manifest.files[PATH].server_revision, 1)


class ManualConflictAttachmentTests(TestCase):
    """Manual-policy conflicts on attachments keep the extension and leave
    the original file untouched (no textual merge is attempted)."""

    def test_write_conflict_preserves_extension_and_leaves_local_untouched(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            destination = root / PATH
            destination.parent.mkdir(parents=True, exist_ok=True)
            local_raw = b'local-dirty-bytes'
            destination.write_bytes(local_raw)
            server_raw = b'server-bytes'
            server_hash = sha256_bytes(server_raw)
            manifest = Manifest(vault_id=VAULT_ID, device_id=DEVICE_ID)
            manifest.files[PATH] = ManifestEntry(
                server_revision=4,
                content_hash=sha256_bytes(b'old-synced-bytes'),
                last_synced_at='2026-07-01T00:00:00+00:00',
            )

            def handler(request: httpx.Request) -> httpx.Response:
                self.assertEqual(request.method, 'GET')
                return _success_response(
                    {
                        'vault_id': VAULT_ID,
                        'path': PATH,
                        'revision': 5,
                        'content_hash': server_hash,
                        'content': base64.b64encode(server_raw).decode('ascii'),
                        'encoding': 'base64',
                        'deleted': False,
                    }
                )

            change = SyncChangeItem(
                revision=5,
                path=PATH,
                event_type='UPDATE',
                content_hash=server_hash,
                deleted=False,
                created_at=datetime.now(UTC),
            )
            config = _config(root)
            summary = engine.SyncSummary()
            with _make_client(handler) as client:
                engine._apply_write(
                    config,
                    manifest,
                    client,
                    summary,
                    _LOGGER,
                    change,
                    destination,
                    manifest.files[PATH],
                )

            self.assertEqual(summary.conflicts, [PATH])
            self.assertEqual(destination.read_bytes(), local_raw)

            conflicts = [
                path
                for path in root.rglob('*.png')
                if is_conflict_file(path.relative_to(root).as_posix())
            ]
            self.assertEqual(len(conflicts), 1)
            self.assertEqual(conflicts[0].read_bytes(), server_raw)
            self.assertTrue(conflicts[0].name.endswith('.png'))
