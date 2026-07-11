import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timezone
from pathlib import Path

from obsidian_sync.domain.errors import DomainValidationError
from obsidian_sync.domain.hashing import sha256_file, sha256_text
from obsidian_sync.domain.paths import normalize_source_path, safe_vault_destination
from obsidian_sync.schemas.sync import SyncChangeItem
from obsidian_sync.sync_agent.atomic import write_text_atomic
from obsidian_sync.sync_agent.client import (
    DEFAULT_PAGE_LIMIT,
    SyncApiError,
    SyncClient,
    SyncConflictError,
)
from obsidian_sync.sync_agent.config import AgentConfig
from obsidian_sync.sync_agent.conflict import (
    LOCAL_DELETED_PLACEHOLDER,
    SERVER_DELETED_PLACEHOLDER,
    write_conflict_file,
)
from obsidian_sync.sync_agent.manifest import (
    Manifest,
    ManifestConflict,
    ManifestEntry,
    load_manifest,
    save_manifest,
)
from obsidian_sync.sync_agent.obsidian import refresh_obsidian
from obsidian_sync.sync_agent.scanner import (
    LocalChanges,
    classify_local_changes,
    scan_vault,
)

DELETE_EVENTS = frozenset({'DELETE'})


@dataclass(slots=True)
class SyncSummary:
    pulled: int = 0
    applied: int = 0
    locally_deleted: int = 0
    pushed: int = 0
    remotely_deleted: int = 0
    conflicts: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    obsidian_ok: bool = True
    obsidian_message: str = ''
    dry_run: bool = False


class SyncError(Exception):
    """Raised for unrecoverable sync failures (hard errors)."""


def run_sync(
    config: AgentConfig,
    *,
    dry_run: bool,
    logger: logging.Logger,
) -> SyncSummary:
    summary = SyncSummary(dry_run=dry_run)
    manifest = _load_and_validate_manifest(config)

    try:
        with SyncClient(
            config.server_base_url,
            config.api_token,
            max_retries=config.max_retries,
            retry_base_delay=config.retry_base_delay,
            retry_max_delay=config.retry_max_delay,
            logger=logger,
        ) as client:
            if dry_run:
                _plan(config, manifest, client, summary, logger)
                return summary
            _run(config, manifest, client, summary, logger)
    except SyncApiError as exc:
        raise SyncError(str(exc)) from exc

    save_manifest(config.vault_root, manifest)
    _run_obsidian(config, summary, logger)
    return summary


def run_status(config: AgentConfig, logger: logging.Logger) -> None:
    try:
        with SyncClient(
            config.server_base_url,
            config.api_token,
            max_retries=config.max_retries,
            retry_base_delay=config.retry_base_delay,
            retry_max_delay=config.retry_max_delay,
            logger=logger,
        ) as client:
            status = client.get_status(config.vault_id, device_id=config.device_id)
    except SyncApiError as exc:
        raise SyncError(str(exc)) from exc
    manifest = load_manifest(config.vault_root)
    logger.info('vault: %s', status.vault_id)
    logger.info('device: %s', config.device_id)
    logger.info('server revision: %s', status.server_revision)
    logger.info('device last seen revision: %s', status.device_last_seen_revision)
    logger.info('local manifest cursor: %s', manifest.last_sync_cursor)
    logger.info('tracked local files: %s', len(manifest.files))
    logger.info('pending changes: %s', status.pending_changes)
    logger.info('open conflicts: %s', status.open_conflicts)
    logger.info('pending vectorizing jobs: %s', status.pending_vectorizing_jobs)


def _run(
    config: AgentConfig,
    manifest: Manifest,
    client: SyncClient,
    summary: SyncSummary,
    logger: logging.Logger,
) -> None:
    client.register_device(
        config.vault_id,
        device_id=config.device_id,
        device_name=config.device_name,
    )
    logger.debug('registered device %s', config.device_id)

    _pull(config, manifest, client, summary, logger, device_id=None)

    # Paths that already produced a pull-side conflict must not be re-pushed in
    # the same run: the PUT is doomed to 409 and would spawn a second conflict
    # file for the same divergence.
    pull_conflicts = set(summary.conflicts)

    scanned = scan_vault(config.vault_root)
    local = classify_local_changes(scanned, manifest)
    _push(config, manifest, client, summary, logger, local, skip_paths=pull_conflicts)

    # Advance the cursor past our own just-pushed events (and any concurrent
    # changes from other devices), touching this device's last-seen revision.
    _pull(config, manifest, client, summary, logger, device_id=config.device_id)


def _plan(
    config: AgentConfig,
    manifest: Manifest,
    client: SyncClient,
    summary: SyncSummary,
    logger: logging.Logger,
) -> None:
    cursor = manifest.last_sync_cursor
    while True:
        page = client.get_changes(
            config.vault_id, since=cursor, limit=DEFAULT_PAGE_LIMIT
        )
        for change in page.changes:
            summary.pulled += 1
            logger.info(
                'would apply %s %s (revision %s)',
                change.event_type,
                change.path,
                change.revision,
            )
        if not page.changes or page.to_cursor <= cursor:
            break
        cursor = page.to_cursor

    scanned = scan_vault(config.vault_root)
    local = classify_local_changes(scanned, manifest)
    for path in local.new:
        logger.info('would create %s on server', path)
    for path in local.modified:
        logger.info('would update %s on server', path)
    for path in local.deleted:
        logger.info('would delete %s on server', path)
    summary.pushed = len(local.new) + len(local.modified)
    summary.remotely_deleted = len(local.deleted)


def _pull(
    config: AgentConfig,
    manifest: Manifest,
    client: SyncClient,
    summary: SyncSummary,
    logger: logging.Logger,
    *,
    device_id: str | None,
) -> None:
    cursor = manifest.last_sync_cursor
    while True:
        page = client.get_changes(
            config.vault_id,
            since=cursor,
            device_id=device_id,
            limit=DEFAULT_PAGE_LIMIT,
        )
        for change in page.changes:
            summary.pulled += 1
            _apply_change(config, manifest, client, summary, logger, change)
        if not page.changes or page.to_cursor <= cursor:
            cursor = max(cursor, page.to_cursor)
            break
        cursor = page.to_cursor
    manifest.last_sync_cursor = max(manifest.last_sync_cursor, cursor)


def _apply_change(
    config: AgentConfig,
    manifest: Manifest,
    client: SyncClient,
    summary: SyncSummary,
    logger: logging.Logger,
    change: SyncChangeItem,
) -> None:
    path = change.path
    try:
        normalize_source_path(path)
        destination = safe_vault_destination(config.vault_root, path)
    except DomainValidationError:
        message = f'rejected unsafe server path: {path}'
        logger.warning(message)
        summary.warnings.append(message)
        return

    entry = manifest.files.get(path)
    is_delete = change.event_type in DELETE_EVENTS or change.deleted

    if not is_delete and entry is not None and change.content_hash is not None:
        if entry.content_hash == change.content_hash:
            entry.server_revision = change.revision
            logger.debug('no-op change for %s (revision %s)', path, change.revision)
            return

    if is_delete:
        _apply_delete(config, manifest, summary, logger, change, destination, entry)
        return
    _apply_write(config, manifest, client, summary, logger, change, destination, entry)


def _apply_delete(
    config: AgentConfig,
    manifest: Manifest,
    summary: SyncSummary,
    logger: logging.Logger,
    change: SyncChangeItem,
    destination: Path,
    entry: ManifestEntry | None,
) -> None:
    path = change.path
    if not destination.exists():
        manifest.files.pop(path, None)
        logger.debug('delete %s already applied locally', path)
        return
    local_hash = sha256_file(destination)
    if entry is not None and local_hash == entry.content_hash:
        destination.unlink()
        manifest.files.pop(path, None)
        manifest.conflicts.pop(path, None)
        summary.locally_deleted += 1
        logger.info('deleted %s (server revision %s)', path, change.revision)
        return
    conflict = write_conflict_file(
        config.vault_root,
        path,
        device_id=config.device_id,
        client_base_revision=entry.server_revision if entry else 0,
        server_revision=change.revision,
        local_content=destination.read_text(encoding='utf-8'),
        server_content=SERVER_DELETED_PLACEHOLDER,
    )
    manifest.conflicts[path] = ManifestConflict(
        server_revision=change.revision,
        server_content_hash=change.content_hash,
        local_content_hash=local_hash,
        server_deleted=True,
    )
    summary.conflicts.append(path)
    logger.warning('conflict on delete of %s; wrote %s', path, conflict.name)


def _apply_write(
    config: AgentConfig,
    manifest: Manifest,
    client: SyncClient,
    summary: SyncSummary,
    logger: logging.Logger,
    change: SyncChangeItem,
    destination: Path,
    entry: ManifestEntry | None,
) -> None:
    path = change.path
    server_file = client.get_file(config.vault_id, path)

    if sha256_text(server_file.content) != server_file.content_hash:
        message = f'server content hash mismatch for {path}; skipped'
        logger.warning(message)
        summary.warnings.append(message)
        return

    if destination.exists():
        local_hash = sha256_file(destination)
        expected = entry.content_hash if entry else None
        dirty = (
            local_hash != expected
            if expected is not None
            else local_hash != server_file.content_hash
        )
        if dirty:
            conflict = write_conflict_file(
                config.vault_root,
                path,
                device_id=config.device_id,
                client_base_revision=entry.server_revision if entry else 0,
                server_revision=change.revision,
                local_content=destination.read_text(encoding='utf-8'),
                server_content=server_file.content,
            )
            manifest.conflicts[path] = ManifestConflict(
                server_revision=server_file.revision,
                server_content_hash=server_file.content_hash,
                local_content_hash=local_hash,
                server_deleted=False,
            )
            summary.conflicts.append(path)
            logger.warning('conflict applying %s; wrote %s', path, conflict.name)
            return

    write_text_atomic(destination, server_file.content)
    manifest.files[path] = ManifestEntry(
        server_revision=server_file.revision,
        content_hash=server_file.content_hash,
        last_synced_at=_now_iso(),
    )
    manifest.conflicts.pop(path, None)
    summary.applied += 1
    logger.info(
        'applied %s %s (revision %s)',
        change.event_type,
        path,
        server_file.revision,
    )


def _push(
    config: AgentConfig,
    manifest: Manifest,
    client: SyncClient,
    summary: SyncSummary,
    logger: logging.Logger,
    local: LocalChanges,
    *,
    skip_paths: set[str],
) -> None:
    for path in local.new:
        if path in skip_paths:
            continue
        _push_upsert(config, manifest, client, summary, logger, path, base_revision=0)
    for path in local.modified:
        if path in skip_paths:
            continue
        entry = manifest.files.get(path)
        conflict = manifest.conflicts.get(path)
        if conflict is not None:
            destination = safe_vault_destination(config.vault_root, path)
            if sha256_file(destination) == conflict.local_content_hash:
                continue
            base = conflict.server_revision
        else:
            base = entry.server_revision if entry else 0
        _push_upsert(
            config, manifest, client, summary, logger, path, base_revision=base
        )
    for path in local.deleted:
        if path in skip_paths:
            continue
        _push_delete(config, manifest, client, summary, logger, path)


def _push_upsert(
    config: AgentConfig,
    manifest: Manifest,
    client: SyncClient,
    summary: SyncSummary,
    logger: logging.Logger,
    path: str,
    *,
    base_revision: int,
) -> None:
    destination = safe_vault_destination(config.vault_root, path)
    content = destination.read_text(encoding='utf-8')
    content_hash = sha256_text(content)
    try:
        result = client.put_file(
            config.vault_id,
            path,
            device_id=config.device_id,
            base_revision=base_revision,
            content_hash=content_hash,
            content=content,
        )
    except SyncConflictError as exc:
        _push_conflict(config, client, summary, logger, path, exc, content)
        return
    manifest.files[path] = ManifestEntry(
        server_revision=result.revision,
        content_hash=result.content_hash,
        last_synced_at=_now_iso(),
    )
    manifest.conflicts.pop(path, None)
    summary.pushed += 1
    logger.info('pushed %s (revision %s)', path, result.revision)


def _push_delete(
    config: AgentConfig,
    manifest: Manifest,
    client: SyncClient,
    summary: SyncSummary,
    logger: logging.Logger,
    path: str,
) -> None:
    entry = manifest.files.get(path)
    conflict = manifest.conflicts.get(path)
    if conflict is not None and conflict.server_deleted:
        manifest.files.pop(path, None)
        manifest.conflicts.pop(path, None)
        logger.info('accepted server delete for %s', path)
        return
    base_revision = (
        conflict.server_revision
        if conflict is not None
        else entry.server_revision
        if entry
        else 0
    )
    try:
        client.delete_file(
            config.vault_id,
            path,
            device_id=config.device_id,
            base_revision=base_revision,
        )
    except SyncConflictError as exc:
        _push_conflict(
            config, client, summary, logger, path, exc, LOCAL_DELETED_PLACEHOLDER
        )
        return
    manifest.files.pop(path, None)
    manifest.conflicts.pop(path, None)
    summary.remotely_deleted += 1
    logger.info('deleted %s on server', path)


def _push_conflict(
    config: AgentConfig,
    client: SyncClient,
    summary: SyncSummary,
    logger: logging.Logger,
    path: str,
    exc: SyncConflictError,
    local_content: str,
) -> None:
    server_revision = _int_detail(exc.details.get('server_revision'))
    client_base = _int_detail(exc.details.get('client_base_revision'))
    try:
        server_content = client.get_file(config.vault_id, path).content
    except SyncApiError:
        server_content = SERVER_DELETED_PLACEHOLDER
    conflict = write_conflict_file(
        config.vault_root,
        path,
        device_id=config.device_id,
        client_base_revision=client_base,
        server_revision=server_revision,
        local_content=local_content,
        server_content=server_content,
    )
    summary.conflicts.append(path)
    logger.warning('push conflict on %s; wrote %s', path, conflict.name)


def _run_obsidian(
    config: AgentConfig,
    summary: SyncSummary,
    logger: logging.Logger,
) -> None:
    result = refresh_obsidian(config.obsidian, logger)
    summary.obsidian_ok = result.ok
    summary.obsidian_message = result.message
    if result.ok:
        logger.debug('obsidian step: %s', result.message)
        return
    if config.require_obsidian_refresh:
        logger.error('obsidian refresh failed: %s', result.message)
    else:
        logger.warning('obsidian refresh warning: %s', result.message)
        summary.warnings.append(result.message)


def _load_and_validate_manifest(config: AgentConfig) -> Manifest:
    manifest = load_manifest(config.vault_root)
    if manifest.vault_id and manifest.vault_id != config.vault_id:
        raise SyncError(
            f'manifest vault_id {manifest.vault_id!r} does not match '
            f'configured vault_id {config.vault_id!r}'
        )
    manifest.vault_id = config.vault_id
    manifest.device_id = config.device_id
    return manifest


def _int_detail(value: object) -> int:
    return value if isinstance(value, int) else 0


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()
