from pathlib import PurePosixPath

from obsidian_sync.domain.files import ALLOWED_EXTENSIONS
from obsidian_sync.domain.sync_rules import (
    is_conflict_file,
    is_ignored_path,
    is_markdown_path,
)

IGNORED_FILE_NAMES = frozenset({'.DS_Store', 'Thumbs.db'})

# Attachment extensions the agent may sync when `sync_attachments` is
# enabled -- domain.files' allow-list minus markdown, which is always
# handled separately (and unconditionally) below.
_ATTACHMENT_EXTENSIONS = ALLOWED_EXTENSIONS - {'.md'}


def is_ignored_dir(name: str) -> bool:
    """Return True for directories that should never be descended into."""
    if name.startswith('.'):
        return True
    return is_ignored_path(f'{name}/')


def should_sync(rel_path: str, *, sync_attachments: bool = False) -> bool:
    """Return True when a vault-relative path should be synced.

    ``.md`` files are always synced. Attachments (images/PDFs from
    ``domain.files.ALLOWED_EXTENSIONS``) are only synced when
    ``sync_attachments`` is enabled (off by default for backward
    compatibility with v1 agents that only ever synced markdown). Hidden
    files and directories (any segment starting with ``.``), conflict files,
    and the ignored directories from ``domain.sync_rules`` are always
    excluded regardless of file type.
    """
    segments = [segment for segment in rel_path.split('/') if segment]
    if not segments:
        return False
    if segments[-1] in IGNORED_FILE_NAMES:
        return False
    if any(segment.startswith('.') for segment in segments):
        return False
    if is_conflict_file(rel_path):
        return False
    if is_ignored_path(rel_path):
        return False
    if is_markdown_path(rel_path):
        return True
    if not sync_attachments:
        return False
    extension = PurePosixPath(rel_path).suffix.lower()
    return extension in _ATTACHMENT_EXTENSIONS
