# pyright: reportMissingImports=false

import logging
from datetime import UTC, datetime
from pathlib import Path

from obsidian_sync.domain.hashing import sha256_text
from obsidian_sync.schemas.sync import (
    DeleteFileData,
    FileContentData,
    PutFileData,
    RegisterDeviceData,
    SyncChangeItem,
    SyncChangesData,
)
from obsidian_sync.sync_agent import engine
from obsidian_sync.sync_agent.client import SyncApiError
from obsidian_sync.sync_agent.config import AgentConfig
from obsidian_sync.sync_agent.manifest import (
    Manifest,
    ManifestEntry,
    load_manifest,
    save_manifest,
)

BASE_URL = 'https://sync.example'
DEVICE_ID = 'laptop'
VAULT_ID = 'v1'
LOGGER = logging.getLogger('test.engine.manifest_persistence')


class InterruptingClient:
    def __init__(self, *, fail_get_changes_call: int) -> None:
        self.fail_get_changes_call = fail_get_changes_call
        self.get_changes_calls = 0
        self.files: dict[str, FileContentData] = {}
        self.changes: list[SyncChangeItem] = []
        self.next_revision = 10
        self.puts: list[str] = []
        self.deletes: list[str] = []

    def register_device(
        self,
        vault_id: str,
        *,
        device_id: str,
        device_name: str | None,
    ) -> RegisterDeviceData:
        return RegisterDeviceData(
            vault_id=vault_id,
            device_id=device_id,
            registered=True,
        )

    def get_changes(
        self,
        vault_id: str,
        *,
        since: int,
        device_id: str | None = None,
        limit: int,
    ) -> SyncChangesData:
        self.get_changes_calls += 1
        if self.get_changes_calls == self.fail_get_changes_call:
            raise SyncApiError('interrupted get_changes')
        changes = [change for change in self.changes if change.revision > since]
        to_cursor = max([since, *(change.revision for change in changes)])
        return SyncChangesData(
            vault_id=vault_id,
            from_cursor=since,
            to_cursor=to_cursor,
            changes=changes,
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
        self.puts.append(path)
        revision = self.next_revision
        self.next_revision += 1
        return PutFileData(
            vault_id=vault_id,
            path=path,
            revision=revision,
            content_hash=content_hash,
        )

    def delete_file(
        self,
        vault_id: str,
        path: str,
        *,
        device_id: str,
        base_revision: int,
    ) -> DeleteFileData:
        self.deletes.append(path)
        revision = self.next_revision
        self.next_revision += 1
        return DeleteFileData(
            vault_id=vault_id,
            path=path,
            revision=revision,
            deleted=True,
        )


def test_pull_apply_persists_manifest_before_later_pull_failure(tmp_path: Path) -> None:
    config = _config(tmp_path)
    manifest = Manifest(vault_id=VAULT_ID, device_id=DEVICE_ID)
    save_manifest(tmp_path, manifest)
    client = InterruptingClient(fail_get_changes_call=2)
    client.files['new.md'] = FileContentData(
        vault_id=VAULT_ID,
        path='new.md',
        revision=2,
        content_hash=sha256_text('new'),
        content='new',
        deleted=False,
    )
    client.changes = [_change(2, 'new.md', 'CREATE', sha256_text('new'))]

    try:
        engine._run(config, manifest, client, engine.SyncSummary(), LOGGER)
    except SyncApiError:
        pass
    else:  # pragma: no cover - explicit failure path for readability
        raise AssertionError('expected interrupted get_changes')

    persisted = load_manifest(tmp_path)
    assert persisted.files['new.md'].server_revision == 2
    assert persisted.last_sync_cursor == 0
    assert (tmp_path / 'new.md').read_text(encoding='utf-8') == 'new'


def test_pull_conflict_record_persists_before_later_pull_failure(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    destination = tmp_path / 'note.md'
    destination.write_text('local dirty', encoding='utf-8')
    manifest = Manifest(vault_id=VAULT_ID, device_id=DEVICE_ID)
    manifest.files['note.md'] = ManifestEntry(
        server_revision=1,
        content_hash=sha256_text('old'),
        last_synced_at='2026-07-01T00:00:00+00:00',
    )
    save_manifest(tmp_path, manifest)
    client = InterruptingClient(fail_get_changes_call=2)
    client.files['note.md'] = FileContentData(
        vault_id=VAULT_ID,
        path='note.md',
        revision=2,
        content_hash=sha256_text('server'),
        content='server',
        deleted=False,
    )
    client.changes = [_change(2, 'note.md', 'UPDATE', sha256_text('server'))]

    try:
        engine._run(config, manifest, client, engine.SyncSummary(), LOGGER)
    except SyncApiError:
        pass
    else:  # pragma: no cover - explicit failure path for readability
        raise AssertionError('expected interrupted get_changes')

    persisted = load_manifest(tmp_path)
    assert persisted.conflicts['note.md'].server_revision == 2
    assert persisted.files['note.md'].server_revision == 1


def test_push_and_delete_persist_manifest_before_final_pull_failure(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    (tmp_path / 'new.md').write_text('new', encoding='utf-8')
    manifest = Manifest(vault_id=VAULT_ID, device_id=DEVICE_ID, last_sync_cursor=7)
    manifest.files['removed.md'] = ManifestEntry(
        server_revision=8,
        content_hash=sha256_text('removed'),
        last_synced_at='2026-07-01T00:00:00+00:00',
    )
    save_manifest(tmp_path, manifest)
    client = InterruptingClient(fail_get_changes_call=2)

    try:
        engine._run(config, manifest, client, engine.SyncSummary(), LOGGER)
    except SyncApiError:
        pass
    else:  # pragma: no cover - explicit failure path for readability
        raise AssertionError('expected interrupted get_changes')

    persisted = load_manifest(tmp_path)
    assert persisted.files['new.md'].server_revision == 10
    assert 'removed.md' not in persisted.files
    assert client.puts == ['new.md']
    assert client.deletes == ['removed.md']


def _config(root: Path) -> AgentConfig:
    return AgentConfig(
        server_base_url=BASE_URL,
        vault_id=VAULT_ID,
        vault_root=root,
        device_id=DEVICE_ID,
    )


def _change(
    revision: int,
    path: str,
    event_type: str,
    content_hash: str,
) -> SyncChangeItem:
    return SyncChangeItem(
        revision=revision,
        path=path,
        event_type=event_type,
        content_hash=content_hash,
        deleted=False,
        created_at=datetime.now(UTC),
    )
