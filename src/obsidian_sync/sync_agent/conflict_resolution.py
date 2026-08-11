"""Policy-driven conflict resolution for both the pull and push sync paths.

``engine.py`` detects that a path has diverged (pull side: the server pushed
a change the local file also diverged from; push side: the server rejected
our PUT/DELETE with a 409) and delegates the actual resolution here based on
``config.conflict_policy``:

- ``manual`` (default): write a ``.conflict.*.md`` file and leave both sides
  untouched, exactly as before this policy existed.
- ``local-wins``: retry pushing the local state as the winner via
  ``resolution.resolve_local_wins_upsert``/``resolve_local_wins_delete``,
  bounded by ``resolution.LOCAL_WINS_MAX_ATTEMPTS``. Falls back to the
  manual path if attempts are exhausted.
- ``remote-wins``: adopt the server's state locally, first preserving any
  local content that would otherwise be silently discarded in a
  ``.local-backup.conflict.*.md`` file (the server's own version history
  makes the *server* side always recoverable already; this file is what
  makes the *local* side recoverable too).

Auto-resolved conflicts are never appended to ``summary.conflicts`` -- only
the manual policy (or an exhausted local-wins fallback) is, since that is
what should surface as a non-zero exit code.

``resolve_push_conflict_local_wins``/``resolve_push_conflict_remote_wins``
are also called directly by ``engine._resolve_tracked_conflicts`` to resolve
conflicts left over in ``manifest.conflicts`` from an earlier run, not just
a fresh 409 -- that is why ``resolve_push_conflict_local_wins`` takes the
server revision as an explicit parameter instead of pulling it out of a
``SyncConflictError``, which a tracked conflict does not have.
"""

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from obsidian_sync.domain.hashing import sha256_bytes
from obsidian_sync.domain.paths import safe_vault_destination
from obsidian_sync.domain.sync_rules import is_markdown_path
from obsidian_sync.schemas.sync import FileContentData, SyncChangeItem
from obsidian_sync.sync_agent.atomic import write_bytes_atomic
from obsidian_sync.sync_agent.client import (
    SyncApiError,
    SyncClient,
    SyncConflictError,
    decode_content,
)
from obsidian_sync.sync_agent.config import AgentConfig
from obsidian_sync.sync_agent.conflict import (
    LOCAL_DELETED_PLACEHOLDER,
    SERVER_DELETED_PLACEHOLDER,
    write_attachment_conflict_file,
    write_attachment_local_backup_file,
    write_conflict_file,
    write_local_backup_file,
)
from obsidian_sync.sync_agent.manifest import Manifest, ManifestConflict, ManifestEntry
from obsidian_sync.sync_agent.resolution import (
    resolve_local_wins_delete,
    resolve_local_wins_upsert,
)

if TYPE_CHECKING:
    # Deferred to a type-checking-only import: engine.py imports this module,
    # so importing SyncSummary at runtime here would be circular. The
    # TYPE_CHECKING guard keeps the annotation precise without that cycle.
    from obsidian_sync.sync_agent.engine import SyncSummary


def write_server_content(destination: Path, server_file: FileContentData) -> None:
    """Write server-provided content to disk, decoding per its `encoding`.

    Shared by the pull-apply path (`engine._apply_write`) and the
    conflict-resolution paths below so both write attachments and markdown
    the same way, driven by the server's self-reported encoding rather than
    re-deriving it from the path.
    """
    write_bytes_atomic(destination, decode_content(server_file))


def resolve_pull_delete_conflict(
    config: AgentConfig,
    client: SyncClient,
    manifest: Manifest,
    summary: SyncSummary,
    logger: logging.Logger,
    change: SyncChangeItem,
    destination: Path,
    entry: ManifestEntry | None,
    local_hash: str,
) -> None:
    """Resolve a pull-side delete conflict (server deleted, local diverged)."""
    path = change.path
    policy = config.conflict_policy
    markdown = is_markdown_path(path)
    local_bytes = destination.read_bytes()

    if policy == 'local-wins':
        outcome = resolve_local_wins_upsert(
            client,
            config,
            path=path,
            content=local_bytes,
            base_revision=0,
            logger=logger,
        )
        if outcome.resolved:
            manifest.files[path] = ManifestEntry(
                server_revision=outcome.revision,
                content_hash=outcome.content_hash,
                last_synced_at=_now_iso(),
            )
            manifest.conflicts.pop(path, None)
            summary.pushed += 1
            logger.info(
                'conflict on delete of %s auto-resolved (local-wins): '
                'recreated on server as revision %s',
                path,
                outcome.revision,
            )
            return
        logger.warning(
            'local-wins could not resolve delete conflict on %s; '
            'falling back to manual resolution',
            path,
        )
    elif policy == 'remote-wins':
        if markdown:
            backup = write_local_backup_file(
                config.vault_root,
                path,
                device_id=config.device_id,
                server_revision=change.revision,
                local_content=local_bytes.decode('utf-8'),
            )
        else:
            backup = write_attachment_local_backup_file(
                config.vault_root,
                path,
                device_id=config.device_id,
                local_content=local_bytes,
            )
        destination.unlink()
        manifest.files.pop(path, None)
        manifest.conflicts.pop(path, None)
        summary.locally_deleted += 1
        logger.warning(
            'conflict on delete of %s auto-resolved (remote-wins): '
            'local changes backed up to %s',
            path,
            backup.name,
        )
        return

    if markdown:
        conflict = write_conflict_file(
            config.vault_root,
            path,
            device_id=config.device_id,
            client_base_revision=entry.server_revision if entry else 0,
            server_revision=change.revision,
            local_content=local_bytes.decode('utf-8'),
            server_content=SERVER_DELETED_PLACEHOLDER,
        )
    else:
        # The server has nothing for this path (deleted); the only content
        # worth preserving alongside the (untouched) local file is the
        # local bytes themselves, so the pending divergence is visible.
        conflict = write_attachment_conflict_file(
            config.vault_root,
            path,
            device_id=config.device_id,
            content=local_bytes,
            content_hash=local_hash,
        )
    manifest.conflicts[path] = ManifestConflict(
        server_revision=change.revision,
        server_content_hash=change.content_hash,
        local_content_hash=local_hash,
        server_deleted=True,
    )
    summary.conflicts.append(path)
    logger.warning('conflict on delete of %s; wrote %s', path, conflict.name)


def resolve_pull_write_conflict(
    config: AgentConfig,
    client: SyncClient,
    manifest: Manifest,
    summary: SyncSummary,
    logger: logging.Logger,
    change: SyncChangeItem,
    destination: Path,
    entry: ManifestEntry | None,
    local_hash: str,
    server_file: FileContentData,
) -> None:
    """Resolve a pull-side write conflict (server + local both changed)."""
    path = change.path
    policy = config.conflict_policy
    markdown = is_markdown_path(path)
    local_bytes = destination.read_bytes()

    if policy == 'local-wins':
        outcome = resolve_local_wins_upsert(
            client,
            config,
            path=path,
            content=local_bytes,
            base_revision=server_file.revision,
            logger=logger,
        )
        if outcome.resolved:
            manifest.files[path] = ManifestEntry(
                server_revision=outcome.revision,
                content_hash=outcome.content_hash,
                last_synced_at=_now_iso(),
            )
            manifest.conflicts.pop(path, None)
            summary.pushed += 1
            logger.info(
                'conflict on %s auto-resolved (local-wins): '
                'pushed local content as revision %s',
                path,
                outcome.revision,
            )
            return
        logger.warning(
            'local-wins could not resolve conflict on %s; '
            'falling back to manual resolution',
            path,
        )
    elif policy == 'remote-wins':
        if markdown:
            backup = write_local_backup_file(
                config.vault_root,
                path,
                device_id=config.device_id,
                server_revision=server_file.revision,
                local_content=local_bytes.decode('utf-8'),
            )
        else:
            backup = write_attachment_local_backup_file(
                config.vault_root,
                path,
                device_id=config.device_id,
                local_content=local_bytes,
            )
        write_server_content(destination, server_file)
        manifest.files[path] = ManifestEntry(
            server_revision=server_file.revision,
            content_hash=server_file.content_hash,
            last_synced_at=_now_iso(),
        )
        manifest.conflicts.pop(path, None)
        summary.applied += 1
        logger.warning(
            'conflict on %s auto-resolved (remote-wins): local changes backed up to %s',
            path,
            backup.name,
        )
        return

    if markdown:
        conflict = write_conflict_file(
            config.vault_root,
            path,
            device_id=config.device_id,
            client_base_revision=entry.server_revision if entry else 0,
            server_revision=change.revision,
            local_content=local_bytes.decode('utf-8'),
            server_content=server_file.content,
        )
    else:
        conflict = write_attachment_conflict_file(
            config.vault_root,
            path,
            device_id=config.device_id,
            content=decode_content(server_file),
            content_hash=server_file.content_hash,
        )
    manifest.conflicts[path] = ManifestConflict(
        server_revision=server_file.revision,
        server_content_hash=server_file.content_hash,
        local_content_hash=local_hash,
        server_deleted=False,
    )
    summary.conflicts.append(path)
    logger.warning('conflict applying %s; wrote %s', path, conflict.name)


def resolve_push_conflict(
    config: AgentConfig,
    client: SyncClient,
    manifest: Manifest,
    summary: SyncSummary,
    logger: logging.Logger,
    path: str,
    exc: SyncConflictError,
    local_content: bytes | None,
    *,
    is_delete: bool,
) -> None:
    """Resolve a push-side conflict (our PUT/DELETE was rejected with 409).

    ``local_content`` is the raw local bytes, or ``None`` when
    ``is_delete=True`` (there is nothing local to preserve -- the file was
    already removed locally before the push was attempted).
    """
    policy = config.conflict_policy
    markdown = is_markdown_path(path)
    server_revision = _int_detail(exc.details.get('server_revision'))
    client_base = _int_detail(exc.details.get('client_base_revision'))

    if policy == 'local-wins':
        if resolve_push_conflict_local_wins(
            config,
            client,
            manifest,
            summary,
            logger,
            path,
            local_content,
            server_revision=server_revision,
            is_delete=is_delete,
        ):
            return
        logger.warning(
            'local-wins could not resolve push conflict on %s; '
            'falling back to manual resolution',
            path,
        )
    elif policy == 'remote-wins':
        resolve_push_conflict_remote_wins(
            config,
            client,
            manifest,
            summary,
            logger,
            path,
            local_content,
            is_delete=is_delete,
        )
        return

    try:
        server_file: FileContentData | None = client.get_file(config.vault_id, path)
    except SyncApiError:
        server_file = None

    conflict: Path | None
    if markdown:
        local_text = (
            local_content.decode('utf-8')
            if local_content is not None
            else LOCAL_DELETED_PLACEHOLDER
        )
        server_text = (
            server_file.content
            if server_file is not None
            else SERVER_DELETED_PLACEHOLDER
        )
        conflict = write_conflict_file(
            config.vault_root,
            path,
            device_id=config.device_id,
            client_base_revision=client_base,
            server_revision=server_revision,
            local_content=local_text,
            server_content=server_text,
        )
    elif server_file is not None:
        conflict = write_attachment_conflict_file(
            config.vault_root,
            path,
            device_id=config.device_id,
            content=decode_content(server_file),
            content_hash=server_file.content_hash,
        )
    elif local_content is not None:
        # Neither side has retrievable content to embed and nothing local
        # remains either; there is no bytes payload worth writing.
        conflict = write_attachment_conflict_file(
            config.vault_root,
            path,
            device_id=config.device_id,
            content=local_content,
            content_hash=sha256_bytes(local_content),
        )
    else:
        conflict = None

    summary.conflicts.append(path)
    if conflict is not None:
        logger.warning('push conflict on %s; wrote %s', path, conflict.name)
    else:
        logger.warning('push conflict on %s; no content available to preserve', path)


def resolve_push_conflict_local_wins(
    config: AgentConfig,
    client: SyncClient,
    manifest: Manifest,
    summary: SyncSummary,
    logger: logging.Logger,
    path: str,
    local_content: bytes | None,
    *,
    server_revision: int,
    is_delete: bool,
) -> bool:
    if is_delete:
        resolved = resolve_local_wins_delete(
            client, config, path=path, base_revision=server_revision, logger=logger
        )
        if not resolved:
            return False
        manifest.files.pop(path, None)
        manifest.conflicts.pop(path, None)
        summary.remotely_deleted += 1
        logger.info(
            'push conflict on %s auto-resolved (local-wins): deleted on server', path
        )
        return True

    assert local_content is not None  # guaranteed by callers when not is_delete
    outcome = resolve_local_wins_upsert(
        client,
        config,
        path=path,
        content=local_content,
        base_revision=server_revision,
        logger=logger,
    )
    if not outcome.resolved:
        return False
    manifest.files[path] = ManifestEntry(
        server_revision=outcome.revision,
        content_hash=outcome.content_hash,
        last_synced_at=_now_iso(),
    )
    manifest.conflicts.pop(path, None)
    summary.pushed += 1
    logger.info(
        'push conflict on %s auto-resolved (local-wins): pushed as revision %s',
        path,
        outcome.revision,
    )
    return True


def resolve_push_conflict_remote_wins(
    config: AgentConfig,
    client: SyncClient,
    manifest: Manifest,
    summary: SyncSummary,
    logger: logging.Logger,
    path: str,
    local_content: bytes | None,
    *,
    is_delete: bool,
) -> None:
    destination = safe_vault_destination(config.vault_root, path)
    markdown = is_markdown_path(path)
    try:
        server_file = client.get_file(config.vault_id, path)
    except SyncApiError:
        # The server has no content for this path either (e.g. it was
        # deleted concurrently too); adopt that locally.
        if destination.exists():
            if not is_delete and local_content is not None:
                _write_local_backup(config, path, markdown, local_content, revision=0)
            destination.unlink()
        manifest.files.pop(path, None)
        manifest.conflicts.pop(path, None)
        summary.remotely_deleted += 1
        logger.warning(
            'push conflict on %s auto-resolved (remote-wins): '
            'server has no content; removed locally',
            path,
        )
        return

    if not is_delete and local_content is not None:
        backup = _write_local_backup(
            config, path, markdown, local_content, revision=server_file.revision
        )
        logger.warning(
            'push conflict on %s auto-resolved (remote-wins): '
            'local changes backed up to %s',
            path,
            backup.name,
        )
    else:
        logger.warning(
            'push conflict on %s auto-resolved (remote-wins): '
            'local delete discarded, recreated from server',
            path,
        )

    write_server_content(destination, server_file)
    manifest.files[path] = ManifestEntry(
        server_revision=server_file.revision,
        content_hash=server_file.content_hash,
        last_synced_at=_now_iso(),
    )
    manifest.conflicts.pop(path, None)
    summary.applied += 1


def _write_local_backup(
    config: AgentConfig,
    path: str,
    markdown: bool,
    local_content: bytes,
    *,
    revision: int,
) -> Path:
    if markdown:
        return write_local_backup_file(
            config.vault_root,
            path,
            device_id=config.device_id,
            server_revision=revision,
            local_content=local_content.decode('utf-8'),
        )
    return write_attachment_local_backup_file(
        config.vault_root,
        path,
        device_id=config.device_id,
        local_content=local_content,
    )


def _int_detail(value: object) -> int:
    return value if isinstance(value, int) else 0


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()
