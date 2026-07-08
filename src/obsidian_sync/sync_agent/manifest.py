import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from obsidian_sync.sync_agent.atomic import write_bytes_atomic
from obsidian_sync.sync_agent.config import CONFIG_DIRNAME

MANIFEST_FILENAME = 'manifest.json'


@dataclass(slots=True)
class ManifestEntry:
    server_revision: int
    content_hash: str
    last_synced_at: str


@dataclass(slots=True)
class Manifest:
    vault_id: str = ''
    device_id: str = ''
    last_sync_cursor: int = 0
    files: dict[str, ManifestEntry] = field(default_factory=dict)


def manifest_path(vault_root: Path) -> Path:
    return vault_root / CONFIG_DIRNAME / MANIFEST_FILENAME


def load_manifest(vault_root: Path) -> Manifest:
    path = manifest_path(vault_root)
    if not path.exists():
        return Manifest()
    raw = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(raw, dict):
        return Manifest()
    return _manifest_from_dict(raw)


def save_manifest(vault_root: Path, manifest: Manifest) -> None:
    payload = {
        'vault_id': manifest.vault_id,
        'device_id': manifest.device_id,
        'last_sync_cursor': manifest.last_sync_cursor,
        'files': {
            path: {
                'server_revision': entry.server_revision,
                'content_hash': entry.content_hash,
                'last_synced_at': entry.last_synced_at,
            }
            for path, entry in sorted(manifest.files.items())
        },
    }
    text = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)
    write_bytes_atomic(manifest_path(vault_root), text.encode('utf-8'))


def _manifest_from_dict(raw: dict[str, Any]) -> Manifest:
    files_raw = raw.get('files')
    files: dict[str, ManifestEntry] = {}
    if isinstance(files_raw, dict):
        for path, entry in files_raw.items():
            if not isinstance(entry, dict):
                continue
            files[str(path)] = ManifestEntry(
                server_revision=int(entry.get('server_revision', 0)),
                content_hash=str(entry.get('content_hash', '')),
                last_synced_at=str(entry.get('last_synced_at', '')),
            )
    return Manifest(
        vault_id=str(raw.get('vault_id', '')),
        device_id=str(raw.get('device_id', '')),
        last_sync_cursor=int(raw.get('last_sync_cursor', 0)),
        files=files,
    )
