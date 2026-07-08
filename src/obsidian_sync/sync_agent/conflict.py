import logging
from datetime import datetime
from pathlib import Path, PurePosixPath

from obsidian_sync.domain.paths import safe_vault_destination
from obsidian_sync.sync_agent.atomic import write_text_atomic

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
