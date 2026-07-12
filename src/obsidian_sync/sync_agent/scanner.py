import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from obsidian_sync.domain.hashing import sha256_file
from obsidian_sync.domain.sync_rules import is_markdown_path
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


def scan_vault(
    vault_root: Path,
    *,
    sync_attachments: bool = False,
    attachment_max_bytes: int | None = None,
    on_skipped_oversized: Callable[[str, int], None] | None = None,
) -> dict[str, ScannedFile]:
    """Walk the vault and hash every syncable file.

    Markdown is always included, matching v1 behavior. Attachments
    (images/PDFs) are included only when ``sync_attachments`` is enabled.
    ``attachment_max_bytes`` is an early client-side skip guard -- files over
    it are omitted from the scan (and never pushed); ``on_skipped_oversized``
    is called with ``(path, size_bytes)`` for each one so callers can log a
    warning. The server still enforces its own authoritative per-kind size
    limits on every PUT regardless of what the client filters out first.
    """
    root = vault_root.resolve()
    scanned: dict[str, ScannedFile] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if not is_ignored_dir(name)]
        for filename in filenames:
            full = Path(dirpath) / filename
            rel = full.relative_to(root).as_posix()
            if not should_sync(rel, sync_attachments=sync_attachments):
                continue
            stat = full.stat()
            if (
                attachment_max_bytes is not None
                and not is_markdown_path(rel)
                and stat.st_size > attachment_max_bytes
            ):
                if on_skipped_oversized is not None:
                    on_skipped_oversized(rel, stat.st_size)
                continue
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
