"""Tests for `engine._resolve_tracked_conflicts`, called by `engine._push`.

A conflict recorded in `manifest.conflicts` by an earlier run is only ever
revisited by `resolve_push_conflict`, which fires from the
`SyncConflictError` handlers in `_push_upsert`/`_push_delete` -- i.e. only on
a *fresh* 409. Without `_resolve_tracked_conflicts`, a tracked conflict left
over from a prior run can never be resolved by `--conflict-policy
local-wins`/`remote-wins`; the run reports `conflicts=0` and "sync completed
successfully" while pushing nothing, and the conflict stays in the manifest
forever.

Mirrors `internal/syncagent/engine/conflict_policy_test.go`'s "Tracked
conflicts left over from an earlier run" section, using an in-process fake
client the way `test_engine_manifest_persistence.py` does.
"""

import logging
from pathlib import Path

from obsidian_sync.domain.hashing import sha256_text
from obsidian_sync.domain.sync_rules import is_conflict_file
from obsidian_sync.schemas.sync import (
    DeleteFileData,
    FileContentData,
    PutFileData,
    RegisterDeviceData,
    SyncChangesData,
)
from obsidian_sync.sync_agent import engine
from obsidian_sync.sync_agent.client import SyncConflictError
from obsidian_sync.sync_agent.config import AgentConfig, ConflictPolicy
from obsidian_sync.sync_agent.manifest import Manifest, ManifestConflict, ManifestEntry
from obsidian_sync.sync_agent.scanner import (
    LocalChanges,
    classify_local_changes,
    scan_vault,
)

BASE_URL = 'https://sync.example'
DEVICE_ID = 'laptop'
VAULT_ID = 'v1'
PATH = 'note.md'
_LOGGER = logging.getLogger('test.engine.tracked_conflicts')


class FakeClient:
    def __init__(self) -> None:
        self.files: dict[str, FileContentData] = {}
        self.next_revision = 10
        self.puts: list[tuple[str, int]] = []
        self.deletes: list[tuple[str, int]] = []
        # When set, every put/delete for the path raises a SyncConflictError
        # reporting this server revision, simulating an unresolvable retry.
        self.put_conflict_revision: dict[str, int] = {}
        self.delete_conflict_revision: dict[str, int] = {}

    def register_device(
        self, vault_id: str, *, device_id: str, device_name: str | None
    ) -> RegisterDeviceData:
        return RegisterDeviceData(
            vault_id=vault_id, device_id=device_id, registered=True
        )

    def get_changes(
        self, vault_id: str, *, since: int, device_id: str | None = None, limit: int
    ) -> SyncChangesData:
        return SyncChangesData(
            vault_id=vault_id, from_cursor=since, to_cursor=since, changes=[]
        )

    def get_file(self, vault_id: str, path: str) -> FileContentData:
        return self.files[path]

    def put_file(
        self,
        vault_id: str,
        path: str,
        *,
        device_id: str,
        base_revision: int,
        content_hash: str,
        content: bytes,
    ) -> PutFileData:
        if path in self.put_conflict_revision:
            revision = self.put_conflict_revision[path]
            raise SyncConflictError(
                details={
                    'server_revision': revision,
                    'client_base_revision': base_revision,
                },
                message='revision mismatch',
            )
        self.puts.append((path, base_revision))
        revision = self.next_revision
        self.next_revision += 1
        return PutFileData(
            vault_id=vault_id, path=path, revision=revision, content_hash=content_hash
        )

    def delete_file(
        self, vault_id: str, path: str, *, device_id: str, base_revision: int
    ) -> DeleteFileData:
        if path in self.delete_conflict_revision:
            revision = self.delete_conflict_revision[path]
            raise SyncConflictError(
                details={
                    'server_revision': revision,
                    'client_base_revision': base_revision,
                },
                message='revision mismatch',
            )
        self.deletes.append((path, base_revision))
        revision = self.next_revision
        self.next_revision += 1
        return DeleteFileData(
            vault_id=vault_id, path=path, revision=revision, deleted=True
        )


def _config(root: Path, policy: ConflictPolicy = 'manual') -> AgentConfig:
    return AgentConfig(
        server_base_url=BASE_URL,
        vault_id=VAULT_ID,
        vault_root=root,
        device_id=DEVICE_ID,
        conflict_policy=policy,
    )


def _local_changes(config: AgentConfig, manifest: Manifest) -> LocalChanges:
    scanned = scan_vault(config.vault_root)
    return classify_local_changes(scanned, manifest)


def _backup_files(root: Path) -> list[Path]:
    return [
        path
        for path in root.rglob('*.md')
        if 'local-backup' in path.name and is_conflict_file(path.name)
    ]


def test_local_wins_resolves_leftover_conflict(tmp_path: Path) -> None:
    (tmp_path / PATH).write_text('LOCAL DIRTY CONTENT', encoding='utf-8')
    manifest = Manifest(vault_id=VAULT_ID, device_id=DEVICE_ID)
    manifest.files[PATH] = ManifestEntry(
        server_revision=4,
        content_hash=sha256_text('OLD SYNCED CONTENT'),
        last_synced_at='2026-07-01T00:00:00+00:00',
    )
    manifest.conflicts[PATH] = ManifestConflict(
        server_revision=5,
        server_content_hash=sha256_text('SERVER CONTENT'),
        local_content_hash=sha256_text('LOCAL DIRTY CONTENT'),
        server_deleted=False,
    )
    config = _config(tmp_path, 'local-wins')
    local = _local_changes(config, manifest)
    client = FakeClient()
    client.next_revision = 10
    summary = engine.SyncSummary()

    engine._push(config, manifest, client, summary, _LOGGER, local, skip_paths=set())

    assert summary.conflicts == []
    assert summary.pushed == 1
    assert client.puts == [(PATH, 5)]
    assert (tmp_path / PATH).read_text(encoding='utf-8') == 'LOCAL DIRTY CONTENT'
    assert manifest.files[PATH].server_revision == 10
    assert PATH not in manifest.conflicts


def test_remote_wins_resolves_leftover_conflict(tmp_path: Path) -> None:
    (tmp_path / PATH).write_text('LOCAL DIRTY CONTENT', encoding='utf-8')
    manifest = Manifest(vault_id=VAULT_ID, device_id=DEVICE_ID)
    manifest.files[PATH] = ManifestEntry(
        server_revision=4,
        content_hash=sha256_text('OLD SYNCED CONTENT'),
        last_synced_at='2026-07-01T00:00:00+00:00',
    )
    manifest.conflicts[PATH] = ManifestConflict(
        server_revision=5,
        server_content_hash=sha256_text('SERVER CONTENT'),
        local_content_hash=sha256_text('LOCAL DIRTY CONTENT'),
        server_deleted=False,
    )
    config = _config(tmp_path, 'remote-wins')
    local = _local_changes(config, manifest)
    client = FakeClient()
    client.files[PATH] = FileContentData(
        vault_id=VAULT_ID,
        path=PATH,
        revision=5,
        content_hash=sha256_text('SERVER CONTENT'),
        content='SERVER CONTENT',
        deleted=False,
    )
    summary = engine.SyncSummary()

    engine._push(config, manifest, client, summary, _LOGGER, local, skip_paths=set())

    assert summary.conflicts == []
    assert summary.applied == 1
    assert client.puts == []
    assert client.deletes == []
    assert (tmp_path / PATH).read_text(encoding='utf-8') == 'SERVER CONTENT'
    assert manifest.files[PATH].server_revision == 5
    assert PATH not in manifest.conflicts
    backups = _backup_files(tmp_path)
    assert len(backups) == 1
    body = backups[0].read_text(encoding='utf-8')
    assert 'remote-wins' in body
    assert 'LOCAL DIRTY CONTENT' in body


def test_manual_leaves_leftover_conflict_untouched(tmp_path: Path) -> None:
    (tmp_path / PATH).write_text('LOCAL DIRTY CONTENT', encoding='utf-8')
    manifest = Manifest(vault_id=VAULT_ID, device_id=DEVICE_ID)
    manifest.files[PATH] = ManifestEntry(
        server_revision=4,
        content_hash=sha256_text('OLD SYNCED CONTENT'),
        last_synced_at='2026-07-01T00:00:00+00:00',
    )
    tracked = ManifestConflict(
        server_revision=5,
        server_content_hash=sha256_text('SERVER CONTENT'),
        local_content_hash=sha256_text('LOCAL DIRTY CONTENT'),
        server_deleted=False,
    )
    manifest.conflicts[PATH] = tracked
    config = _config(tmp_path, 'manual')
    local = _local_changes(config, manifest)
    client = FakeClient()
    summary = engine.SyncSummary()

    engine._push(config, manifest, client, summary, _LOGGER, local, skip_paths=set())

    assert client.puts == []
    assert client.deletes == []
    assert (tmp_path / PATH).read_text(encoding='utf-8') == 'LOCAL DIRTY CONTENT'
    assert manifest.conflicts[PATH] == tracked


def test_both_sides_deleted_clears_cleanly(tmp_path: Path) -> None:
    # No local file: it was removed manually rather than resolved via the
    # conflict file, and the server side was already recorded as deleted.
    manifest = Manifest(vault_id=VAULT_ID, device_id=DEVICE_ID)
    manifest.files[PATH] = ManifestEntry(
        server_revision=4,
        content_hash=sha256_text('OLD SYNCED CONTENT'),
        last_synced_at='2026-07-01T00:00:00+00:00',
    )
    manifest.conflicts[PATH] = ManifestConflict(
        server_revision=5,
        server_content_hash=None,
        local_content_hash=sha256_text('LOCAL DIRTY CONTENT'),
        server_deleted=True,
    )
    config = _config(tmp_path, 'local-wins')
    local = _local_changes(config, manifest)
    client = FakeClient()
    summary = engine.SyncSummary()

    engine._push(config, manifest, client, summary, _LOGGER, local, skip_paths=set())

    assert client.puts == []
    assert client.deletes == []
    assert summary.conflicts == []
    assert PATH not in manifest.files
    assert PATH not in manifest.conflicts


def test_local_wins_unresolvable_warns_and_keeps_conflict(tmp_path: Path) -> None:
    (tmp_path / PATH).write_text('LOCAL DIRTY CONTENT', encoding='utf-8')
    manifest = Manifest(vault_id=VAULT_ID, device_id=DEVICE_ID)
    manifest.files[PATH] = ManifestEntry(
        server_revision=4,
        content_hash=sha256_text('OLD SYNCED CONTENT'),
        last_synced_at='2026-07-01T00:00:00+00:00',
    )
    tracked = ManifestConflict(
        server_revision=5,
        server_content_hash=sha256_text('SERVER CONTENT'),
        local_content_hash=sha256_text('LOCAL DIRTY CONTENT'),
        server_deleted=False,
    )
    manifest.conflicts[PATH] = tracked
    config = _config(tmp_path, 'local-wins')
    local = _local_changes(config, manifest)
    client = FakeClient()
    client.put_conflict_revision[PATH] = 5
    summary = engine.SyncSummary()

    engine._push(config, manifest, client, summary, _LOGGER, local, skip_paths=set())

    assert len(summary.warnings) == 1
    assert PATH in summary.warnings[0]
    assert (tmp_path / PATH).read_text(encoding='utf-8') == 'LOCAL DIRTY CONTENT'
    assert manifest.conflicts[PATH] == tracked
