import os
from dataclasses import dataclass, field
from pathlib import Path

from obsidian_sync.domain.hashing import sha256_file
from obsidian_sync.sync_agent.ignore import is_ignored_dir, should_sync
from obsidian_sync.sync_agent.manifest import Manifest


@dataclass(frozen=True, slots=True)
class ScannedFile:
    path: str
    size: int
    mtime: float
    content_hash: str


@dataclass(frozen=True, slots=True)
class LocalChanges:
    new: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)


def scan_vault(vault_root: Path) -> dict[str, ScannedFile]:
    """Walk the vault and hash every syncable markdown file."""
    root = vault_root.resolve()
    scanned: dict[str, ScannedFile] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if not is_ignored_dir(name)]
        for filename in filenames:
            full = Path(dirpath) / filename
            rel = full.relative_to(root).as_posix()
            if not should_sync(rel):
                continue
            stat = full.stat()
            scanned[rel] = ScannedFile(
                path=rel,
                size=stat.st_size,
                mtime=stat.st_mtime,
                content_hash=sha256_file(full),
            )
    return scanned


def classify_local_changes(
    scanned: dict[str, ScannedFile],
    manifest: Manifest,
) -> LocalChanges:
    new: list[str] = []
    modified: list[str] = []
    deleted: list[str] = []
    for path, scanned_file in scanned.items():
        entry = manifest.files.get(path)
        if entry is None:
            new.append(path)
        elif entry.content_hash != scanned_file.content_hash:
            modified.append(path)
    for path in manifest.files:
        if path not in scanned:
            deleted.append(path)
    return LocalChanges(
        new=sorted(new),
        modified=sorted(modified),
        deleted=sorted(deleted),
    )
