import logging
from datetime import datetime
from pathlib import Path, PurePosixPath

from obsidian_sync.domain.hashing import sha256_file
from obsidian_sync.domain.paths import safe_vault_destination
from obsidian_sync.sync_agent.atomic import write_bytes_atomic, write_text_atomic

SERVER_DELETED_PLACEHOLDER = '(file is deleted on the server)'
LOCAL_DELETED_PLACEHOLDER = '(file was deleted locally)'
_SERVER_REVISION_PREFIX = '- Server revision:'

_LOGGER = logging.getLogger(__name__)


def conflict_filename(path: str, *, device_id: str, moment: datetime) -> str:
    stem = PurePosixPath(path).stem
    timestamp = moment.strftime('%Y%m%d-%H%M%S')
    return f'{stem}.conflict.{device_id}.{timestamp}.md'


def write_conflict_file(
    vault_root: Path,
    path: str,
    *,
    device_id: str,
    client_base_revision: int,
    server_revision: int,
    local_content: str,
    server_content: str,
) -> Path:
    """Write a conflict copy next to the file and return its path.

    If a conflict file for the same path, device, and server revision already
    exists, it is reused instead of writing another timestamped copy. This
    prevents conflict-file proliferation on a persistently diverged path.
    """
    moment = datetime.now().astimezone()
    parent = PurePosixPath(path).parent
    filename = conflict_filename(path, device_id=device_id, moment=moment)
    relative = (
        filename
        if parent in (PurePosixPath('.'), PurePosixPath(''))
        else (f'{parent.as_posix()}/{filename}')
    )
    destination = safe_vault_destination(vault_root, relative)
    existing = _find_existing_conflict(
        destination.parent,
        stem=PurePosixPath(path).stem,
        device_id=device_id,
        server_revision=server_revision,
    )
    if existing is not None:
        _LOGGER.info(
            'reusing conflict file %s for %s (server revision %s)',
            existing.name,
            path,
            server_revision,
        )
        return existing
    body = (
        '# Sync Conflict\n\n'
        f'- Path: {path}\n'
        f'- Device: {device_id}\n'
        f'- Client base revision: {client_base_revision}\n'
        f'- Server revision: {server_revision}\n'
        f'- Created at: {moment.isoformat()}\n\n'
        '## Local Version\n\n'
        f'{local_content}\n\n'
        '## Server Version\n\n'
        f'{server_content}\n'
    )
    write_text_atomic(destination, body)
    return destination


def local_backup_filename(path: str, *, device_id: str, moment: datetime) -> str:
    """Build the filename for a remote-wins local backup.

    The name deliberately contains the ``.conflict.`` marker used by
    ``domain.sync_rules.is_conflict_file`` so the backup is excluded from
    scanning, pushing, and server-side vectorizing just like a manual
    conflict file, while still being distinguishable from one (different
    prefix, so it never collides with manual-conflict dedup lookups).
    """
    stem = PurePosixPath(path).stem
    timestamp = moment.strftime('%Y%m%d-%H%M%S')
    return f'{stem}.local-backup.conflict.{device_id}.{timestamp}.md'


def write_local_backup_file(
    vault_root: Path,
    path: str,
    *,
    device_id: str,
    server_revision: int,
    local_content: str,
) -> Path:
    """Preserve local content discarded by a remote-wins auto-resolution.

    Called instead of overwriting or deleting local content silently: the
    server keeps a full version history so the server side is always
    recoverable, but the locally-discarded content only survives here.
    """
    moment = datetime.now().astimezone()
    parent = PurePosixPath(path).parent
    filename = local_backup_filename(path, device_id=device_id, moment=moment)
    relative = (
        filename
        if parent in (PurePosixPath('.'), PurePosixPath(''))
        else (f'{parent.as_posix()}/{filename}')
    )
    destination = safe_vault_destination(vault_root, relative)
    body = (
        '# Sync Conflict (auto-resolved: remote-wins)\n\n'
        f'- Path: {path}\n'
        f'- Device: {device_id}\n'
        f'- Server revision applied: {server_revision}\n'
        f'- Created at: {moment.isoformat()}\n\n'
        '## Local Version (discarded)\n\n'
        f'{local_content}\n'
    )
    write_text_atomic(destination, body)
    return destination


def _find_existing_conflict(
    directory: Path,
    *,
    stem: str,
    device_id: str,
    server_revision: int,
) -> Path | None:
    if not directory.exists():
        return None
    prefix = f'{stem}.conflict.{device_id}.'
    for candidate in sorted(directory.iterdir()):
        name = candidate.name
        if not (name.startswith(prefix) and name.endswith('.md')):
            continue
        if _parse_server_revision(candidate) == server_revision:
            return candidate
    return None


def _parse_server_revision(path: Path) -> int | None:
    try:
        text = path.read_text(encoding='utf-8')
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith(_SERVER_REVISION_PREFIX):
            value = line[len(_SERVER_REVISION_PREFIX) :].strip()
            try:
                return int(value)
            except ValueError:
                return None
    return None


def _conflict_relative_path(path: str, filename: str) -> str:
    parent = PurePosixPath(path).parent
    if parent in (PurePosixPath('.'), PurePosixPath('')):
        return filename
    return f'{parent.as_posix()}/{filename}'


def attachment_conflict_filename(path: str, *, device_id: str, moment: datetime) -> str:
    """Build a conflict filename for an attachment, extension preserved.

    Unlike ``conflict_filename`` (markdown), the original extension is kept
    instead of being replaced with ``.md`` (e.g. ``img.conflict.laptop.
    20260707-120000.png``), since the file must remain openable as its
    original type.
    """
    stem = PurePosixPath(path).stem
    extension = PurePosixPath(path).suffix
    timestamp = moment.strftime('%Y%m%d-%H%M%S')
    return f'{stem}.conflict.{device_id}.{timestamp}{extension}'


def write_attachment_conflict_file(
    vault_root: Path,
    path: str,
    *,
    device_id: str,
    content: bytes,
    content_hash: str,
) -> Path:
    """Write a side-by-side attachment conflict copy, extension preserved.

    Binary content cannot be textually merged or embedded in a single
    conflict report the way markdown conflicts embed both versions as
    readable sections, so the resolution here is a side-by-side copy: the
    original file at ``path`` is left untouched, and ``content`` (the
    "other" version relevant to the conflict -- typically the server's, or
    the local one when the server has nothing) is written to a sibling file
    whose name keeps the ``.conflict.`` marker (excluding it from future
    scanning/pushing/vectorizing) and the original extension.

    Deduplicated the same way markdown conflicts are: if a conflict file for
    the same (path, device) with matching content already exists, it is
    reused instead of writing another timestamped copy. Markdown dedup keys
    off an embedded ``- Server revision:`` text line; binary files have no
    such line, so this keys off a direct content-hash comparison instead.
    """
    moment = datetime.now().astimezone()
    filename = attachment_conflict_filename(path, device_id=device_id, moment=moment)
    destination = safe_vault_destination(
        vault_root, _conflict_relative_path(path, filename)
    )
    existing = _find_existing_attachment_conflict(
        destination.parent,
        stem=PurePosixPath(path).stem,
        extension=PurePosixPath(path).suffix,
        device_id=device_id,
        content_hash=content_hash,
    )
    if existing is not None:
        _LOGGER.info('reusing attachment conflict file %s for %s', existing.name, path)
        return existing
    write_bytes_atomic(destination, content)
    return destination


def attachment_local_backup_filename(
    path: str, *, device_id: str, moment: datetime
) -> str:
    stem = PurePosixPath(path).stem
    extension = PurePosixPath(path).suffix
    timestamp = moment.strftime('%Y%m%d-%H%M%S')
    return f'{stem}.local-backup.conflict.{device_id}.{timestamp}{extension}'


def write_attachment_local_backup_file(
    vault_root: Path,
    path: str,
    *,
    device_id: str,
    local_content: bytes,
) -> Path:
    """Preserve local attachment bytes discarded by a remote-wins resolution.

    Binary counterpart of ``write_local_backup_file``: no dedup (matching
    that function), since each remote-wins resolution is a distinct event
    worth preserving on its own.
    """
    moment = datetime.now().astimezone()
    filename = attachment_local_backup_filename(
        path, device_id=device_id, moment=moment
    )
    destination = safe_vault_destination(
        vault_root, _conflict_relative_path(path, filename)
    )
    write_bytes_atomic(destination, local_content)
    return destination


def _find_existing_attachment_conflict(
    directory: Path,
    *,
    stem: str,
    extension: str,
    device_id: str,
    content_hash: str,
) -> Path | None:
    if not directory.exists():
        return None
    prefix = f'{stem}.conflict.{device_id}.'
    for candidate in sorted(directory.iterdir()):
        name = candidate.name
        if not (name.startswith(prefix) and name.endswith(extension)):
            continue
        try:
            if sha256_file(candidate) == content_hash:
                return candidate
        except OSError:
            continue
    return None
