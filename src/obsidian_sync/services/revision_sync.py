import base64
import binascii
import logging
from typing import Literal, NoReturn

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from obsidian_sync.core.config import Settings
from obsidian_sync.core.exceptions import AppError, ErrorCode
from obsidian_sync.db.models import Vault, VaultFile
from obsidian_sync.domain.errors import DomainValidationError
from obsidian_sync.domain.files import (
    FileKind,
    FilePolicy,
    classify_file,
    mime_type_for,
)
from obsidian_sync.domain.hashing import sha256_bytes, verify_sha256
from obsidian_sync.domain.paths import normalize_source_path
from obsidian_sync.domain.sync_rules import is_ignored_path, is_vectorizable_path
from obsidian_sync.repositories.sync import SyncRepository
from obsidian_sync.schemas.sync import (
    ContentEncoding,
    DeleteFileData,
    DeleteFileRequest,
    FileContentData,
    PutFileData,
    PutFileRequest,
    RegisterDeviceData,
    RegisterDeviceRequest,
    RestoreFileRequest,
    SyncChangeItem,
    SyncChangesData,
    SyncStatusData,
)
from obsidian_sync.services.post_sync_indexing import (
    NoopPostSyncIndexDispatcher,
    PostSyncIndexDispatcher,
)
from obsidian_sync.services.storage import StagedReplace, VaultStorage

SyncEventType = Literal['CREATE', 'UPDATE', 'DELETE', 'RESTORE']

_LOGGER = logging.getLogger(__name__)


class RevisionSyncService:
    def __init__(
        self,
        session: AsyncSession,
        storage: VaultStorage,
        settings: Settings,
        post_sync_indexer: PostSyncIndexDispatcher | None = None,
    ) -> None:
        self._session = session
        self._repo = SyncRepository(session)
        self._storage = storage
        self._settings = settings
        self._post_sync_index_dispatcher = (
            post_sync_indexer or NoopPostSyncIndexDispatcher()
        )

    async def register_device(
        self,
        vault_id: str,
        request: RegisterDeviceRequest,
    ) -> RegisterDeviceData:
        vault = await self._require_vault(vault_id)
        device_id = request.device_id.strip()
        if not device_id:
            _raise_validation('device_id is required')
        await self._repo.upsert_device(
            vault=vault,
            device_id=device_id,
            device_name=request.device_name,
        )
        return RegisterDeviceData(
            vault_id=vault.vault_id,
            device_id=device_id,
            registered=True,
        )

    async def list_changes(
        self,
        vault_id: str,
        *,
        since: int,
        device_id: str | None,
        limit: int,
    ) -> SyncChangesData:
        vault = await self._require_vault(vault_id)
        events = await self._repo.list_events_since(
            vault_id=vault.vault_id,
            since=since,
            limit=limit,
        )
        to_cursor = events[-1].revision if events else since
        if device_id:
            await self._repo.touch_device(
                vault=vault,
                device_id=device_id,
                last_seen_revision=to_cursor,
            )
        return SyncChangesData(
            vault_id=vault.vault_id,
            from_cursor=since,
            to_cursor=to_cursor,
            changes=[
                SyncChangeItem(
                    revision=event.revision,
                    path=event.source_path,
                    event_type=event.event_type,
                    content_hash=event.content_hash,
                    deleted=event.deleted,
                    created_at=event.created_at,
                )
                for event in events
            ],
        )

    async def get_status(
        self,
        vault_id: str,
        *,
        device_id: str | None,
    ) -> SyncStatusData:
        vault = await self._require_vault(vault_id)
        server_revision = await self._repo.get_current_revision(vault.id)
        device_last_seen: int | None = None
        if device_id:
            device = await self._repo.get_device(vault.vault_id, device_id)
            if device is not None:
                device_last_seen = device.last_seen_revision
        pending_changes = await self._repo.count_events_since(
            vault_id=vault.vault_id,
            since=device_last_seen or 0,
        )
        open_conflicts = await self._repo.count_open_conflicts(vault.vault_id)
        pending_vectorizing = await self._repo.count_pending_vectorizing(vault.vault_id)
        return SyncStatusData(
            vault_id=vault.vault_id,
            device_id=device_id,
            server_revision=server_revision,
            device_last_seen_revision=device_last_seen,
            pending_changes=pending_changes,
            open_conflicts=open_conflicts,
            pending_vectorizing_jobs=pending_vectorizing,
        )

    async def get_file(self, vault_id: str, file_path: str) -> FileContentData:
        vault = await self._require_vault(vault_id)
        source_path = _normalize_path(file_path)
        row = await self._repo.get_file(vault.vault_id, source_path)
        if row is None or row.deleted:
            _raise_file_not_found(vault.vault_id, source_path)
        policy = self._validate_writable_path(source_path)
        content_bytes, content_hash = await self._read_verified_file(
            vault.vault_id, source_path, row, policy
        )
        content, encoding = _encode_response_content(content_bytes, policy)
        return FileContentData(
            vault_id=vault.vault_id,
            path=source_path,
            revision=row.revision,
            content_hash=content_hash,
            content=content,
            encoding=encoding,
            deleted=row.deleted,
        )

    async def put_file(
        self,
        vault_id: str,
        file_path: str,
        request: PutFileRequest,
        *,
        origin: str | None = None,
    ) -> PutFileData:
        vault = await self._require_vault(vault_id)
        source_path = _normalize_path(file_path)
        policy = self._validate_writable_path(source_path)
        content_bytes = _decode_request_content(request)
        self._validate_content_size(source_path, len(content_bytes), policy)
        content_hash = _verify_hash(content_bytes, request.content_hash)

        row = await self._repo.get_file_for_update(vault.vault_id, source_path)
        event_type = await self._resolve_put_event(
            vault=vault,
            source_path=source_path,
            row=row,
            request=request,
            content_hash=content_hash,
        )
        if event_type is None:
            # Idempotent replay: same base revision and identical content.
            assert row is not None
            return PutFileData(
                vault_id=vault.vault_id,
                path=source_path,
                revision=row.revision,
                content_hash=row.content_hash,
            )

        return await self._write_revision(
            vault=vault,
            source_path=source_path,
            row=row,
            content_bytes=content_bytes,
            content_hash=content_hash,
            event_type=event_type,
            device_id=request.device_id,
            policy=policy,
            origin=origin,
        )

    async def force_put_file(
        self,
        vault_id: str,
        file_path: str,
        *,
        content: str,
        device_id: str,
        origin: str | None = None,
    ) -> PutFileData:
        """Write content unconditionally, still recorded as a revision.

        Used by the one-way MCP sync tool (`VaultSyncService.force_sync_file`)
        once a caller has explicitly opted in to overwriting existing content
        (`overwrite=True`). Unlike `put_file`, this skips the optimistic-
        concurrency `base_revision` check, but it still bumps the vault
        revision, appends a `sync_events` row, and writes version history
        through the same `_write_revision` path as `put_file`, so
        base_revision clients observe the change on their next pull.

        Text-only (``content: str``): the MCP one-way sync path this backs
        (`VaultSyncService.force_sync_file`) is markdown-only upstream, so
        binary attachments never reach this method.
        """
        vault = await self._require_vault(vault_id)
        source_path = _normalize_path(file_path)
        policy = self._validate_writable_path(source_path)
        content_bytes = content.encode('utf-8')
        self._validate_content_size(source_path, len(content_bytes), policy)
        content_hash = sha256_bytes(content_bytes)

        row = await self._repo.get_file_for_update(vault.vault_id, source_path)
        if row is not None and not row.deleted and row.content_hash == content_hash:
            # Idempotent no-op: content already matches, nothing to record.
            return PutFileData(
                vault_id=vault.vault_id,
                path=source_path,
                revision=row.revision,
                content_hash=row.content_hash,
            )
        event_type: SyncEventType = 'CREATE' if row is None or row.deleted else 'UPDATE'

        return await self._write_revision(
            vault=vault,
            source_path=source_path,
            row=row,
            content_bytes=content_bytes,
            content_hash=content_hash,
            event_type=event_type,
            device_id=device_id,
            policy=policy,
            origin=origin,
        )

    async def _write_revision(
        self,
        *,
        vault: Vault,
        source_path: str,
        row: VaultFile | None,
        content_bytes: bytes,
        content_hash: str,
        event_type: SyncEventType,
        device_id: str | None,
        policy: FilePolicy,
        origin: str | None = None,
    ) -> PutFileData:
        """Bump the vault revision and persist content + event (+ version).

        Shared by `put_file` and `force_put_file` once each has already
        decided that a CREATE/UPDATE event should be recorded.

        Version history (`vault_file_versions`) is only written for markdown
        files. Attachments (images/PDFs) can be large and are not
        text-diffable, so keeping an unbounded full-content version per
        revision would risk unbounded storage growth for no real benefit;
        the canonical file on disk is the only copy kept for attachments.
        This also means `restore_file` cannot restore a deleted/overwritten
        attachment (no version to restore from) -- an accepted v1 limitation.
        """
        revision = await self._repo.next_revision(vault.id)
        vectorizable = is_vectorizable_path(source_path)
        mime_type = mime_type_for(policy)
        file_type = str(policy.kind)
        if row is None:
            self._session.add(
                VaultFile(
                    vault_pk=vault.id,
                    vault_id=vault.vault_id,
                    source_path=source_path,
                    content_hash=content_hash,
                    size_bytes=len(content_bytes),
                    mime_type=mime_type,
                    file_type=file_type,
                    vectorize=vectorizable,
                    status='current',
                    index_status='pending' if vectorizable else 'skipped',
                    revision=revision,
                    deleted=False,
                    updated_by_device_id=device_id,
                    last_synced_at=func.now(),
                )
            )
        else:
            row.content_hash = content_hash
            row.size_bytes = len(content_bytes)
            row.mime_type = mime_type
            row.file_type = file_type
            row.revision = revision
            row.deleted = False
            row.deleted_at = None
            row.status = 'current'
            row.vectorize = vectorizable
            row.index_status = 'pending' if vectorizable else 'skipped'
            row.index_error = None
            row.updated_by_device_id = device_id
            row.last_synced_at = func.now()
            row.updated_at = func.now()
        if policy.kind is FileKind.MARKDOWN:
            self._repo.add_version(
                vault=vault,
                source_path=source_path,
                revision=revision,
                content_hash=content_hash,
                content=content_bytes.decode('utf-8'),
                size_bytes=len(content_bytes),
                event_type=event_type,
                device_id=device_id,
            )
        self._repo.add_event(
            vault=vault,
            revision=revision,
            source_path=source_path,
            event_type=event_type,
            content_hash=content_hash,
            deleted=False,
            device_id=device_id,
            origin=origin,
        )

        staged = self._storage.stage_replace(
            vault.vault_id,
            source_path,
            content_bytes,
        )
        await self._commit_staged_file(staged, source_path)
        await self._repo.resolve_open_conflicts(
            vault_id=vault.vault_id, source_path=source_path
        )
        if vectorizable:
            self._post_sync_index_dispatcher.enqueue_file(
                vault_id=vault.vault_id,
                source_path=source_path,
            )
        return PutFileData(
            vault_id=vault.vault_id,
            path=source_path,
            revision=revision,
            content_hash=content_hash,
        )

    async def delete_file(
        self,
        vault_id: str,
        file_path: str,
        request: DeleteFileRequest,
    ) -> DeleteFileData:
        vault = await self._require_vault(vault_id)
        source_path = _normalize_path(file_path)
        row = await self._repo.get_file_for_update(vault.vault_id, source_path)
        if row is None or row.deleted:
            _raise_file_not_found(vault.vault_id, source_path)
        if request.base_revision != row.revision:
            await self._raise_sync_conflict(
                vault=vault,
                source_path=source_path,
                server_revision=row.revision,
                server_content_hash=row.content_hash,
                client_base_revision=request.base_revision,
                device_id=request.device_id,
            )

        revision = await self._repo.next_revision(vault.id)
        last_hash = row.content_hash
        row.revision = revision
        row.deleted = True
        row.deleted_at = func.now()
        row.status = 'archived'
        row.index_status = 'skipped'
        row.index_error = None
        row.updated_by_device_id = request.device_id
        row.updated_at = func.now()
        await self._repo.delete_chunks(vault.vault_id, source_path)
        self._repo.add_event(
            vault=vault,
            revision=revision,
            source_path=source_path,
            event_type='DELETE',
            content_hash=last_hash,
            deleted=True,
            device_id=request.device_id,
        )
        await self._repo.resolve_open_conflicts(
            vault_id=vault.vault_id, source_path=source_path
        )
        return DeleteFileData(
            vault_id=vault.vault_id,
            path=source_path,
            revision=revision,
            deleted=True,
        )

    async def restore_file(
        self,
        vault_id: str,
        request: RestoreFileRequest,
    ) -> PutFileData:
        vault = await self._require_vault(vault_id)
        source_path = _normalize_path(request.path)
        row = await self._repo.get_file_for_update(vault.vault_id, source_path)
        if row is None:
            _raise_file_not_found(vault.vault_id, source_path)
        if not row.deleted:
            raise AppError(
                ErrorCode.CONFLICT_DETECTED,
                'File is active and cannot be restored.',
                status_code=409,
                details={
                    'vault_id': vault.vault_id,
                    'path': source_path,
                    'server_revision': row.revision,
                },
            )

        version = await self._repo.get_version(
            vault_id=vault.vault_id,
            source_path=source_path,
            revision=request.restore_revision,
        )
        if version is None:
            raise AppError(
                ErrorCode.NOT_FOUND,
                'No stored version is available to restore.',
                status_code=404,
                details={
                    'vault_id': vault.vault_id,
                    'path': source_path,
                    'restore_revision': request.restore_revision,
                },
            )

        revision = await self._repo.next_revision(vault.id)
        content_bytes = version.content.encode('utf-8')
        vectorizable = is_vectorizable_path(source_path)
        row.content_hash = version.content_hash
        row.size_bytes = len(content_bytes)
        row.revision = revision
        row.deleted = False
        row.deleted_at = None
        row.status = 'current'
        row.vectorize = vectorizable
        row.index_status = 'pending' if vectorizable else 'skipped'
        row.index_error = None
        row.updated_by_device_id = request.device_id
        row.last_synced_at = func.now()
        row.updated_at = func.now()
        self._repo.add_version(
            vault=vault,
            source_path=source_path,
            revision=revision,
            content_hash=version.content_hash,
            content=version.content,
            size_bytes=len(content_bytes),
            event_type='RESTORE',
            device_id=request.device_id,
        )
        self._repo.add_event(
            vault=vault,
            revision=revision,
            source_path=source_path,
            event_type='RESTORE',
            content_hash=version.content_hash,
            deleted=False,
            device_id=request.device_id,
        )

        staged = self._storage.stage_replace(
            vault.vault_id,
            source_path,
            content_bytes,
        )
        await self._commit_staged_file(staged, source_path)
        await self._repo.resolve_open_conflicts(
            vault_id=vault.vault_id, source_path=source_path
        )
        if vectorizable:
            self._post_sync_index_dispatcher.enqueue_file(
                vault_id=vault.vault_id,
                source_path=source_path,
            )
        return PutFileData(
            vault_id=vault.vault_id,
            path=source_path,
            revision=revision,
            content_hash=version.content_hash,
        )

    async def _resolve_put_event(
        self,
        *,
        vault: Vault,
        source_path: str,
        row: VaultFile | None,
        request: PutFileRequest,
        content_hash: str,
    ) -> SyncEventType | None:
        """Return the event type to record, or None for an idempotent replay."""
        if request.base_revision == 0:
            if row is not None and not row.deleted:
                await self._raise_sync_conflict(
                    vault=vault,
                    source_path=source_path,
                    server_revision=row.revision,
                    server_content_hash=row.content_hash,
                    client_base_revision=request.base_revision,
                    device_id=request.device_id,
                )
            # No row, or a soft-deleted row being recreated: both are CREATE.
            return 'CREATE'

        if row is None or row.deleted:
            await self._raise_sync_conflict(
                vault=vault,
                source_path=source_path,
                server_revision=row.revision if row is not None else 0,
                server_content_hash=row.content_hash if row is not None else None,
                client_base_revision=request.base_revision,
                device_id=request.device_id,
            )
        if request.base_revision != row.revision:
            await self._raise_sync_conflict(
                vault=vault,
                source_path=source_path,
                server_revision=row.revision,
                server_content_hash=row.content_hash,
                client_base_revision=request.base_revision,
                device_id=request.device_id,
            )
        if row.content_hash == content_hash:
            return None
        return 'UPDATE'

    async def _raise_sync_conflict(
        self,
        *,
        vault: Vault,
        source_path: str,
        server_revision: int,
        server_content_hash: str | None,
        client_base_revision: int,
        device_id: str | None,
    ) -> NoReturn:
        self._repo.add_conflict(
            vault=vault,
            source_path=source_path,
            server_revision=server_revision,
            client_base_revision=client_base_revision,
            device_id=device_id,
        )
        # get_db_session rolls back on exceptions, so the conflict row must be
        # committed explicitly before the AppError is raised.
        await self._session.commit()
        raise AppError(
            ErrorCode.SYNC_CONFLICT,
            'Sync conflict detected. Fetch latest changes and retry.',
            status_code=409,
            details={
                'vault_id': vault.vault_id,
                'path': source_path,
                'client_base_revision': client_base_revision,
                'server_revision': server_revision,
                'server_content_hash': server_content_hash,
            },
        )

    async def _commit_staged_file(
        self,
        staged: StagedReplace,
        source_path: str,
    ) -> None:
        try:
            await self._session.flush()
            staged.promote()
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            self._storage.rollback_replace(staged)
            raise AppError(
                ErrorCode.CONFLICT_DETECTED,
                'Conflict detected. Manual verification required.',
                status_code=409,
                details={
                    'conflicts': [
                        {'path': source_path, 'reason': 'database_unique_conflict'}
                    ]
                },
            ) from exc
        except Exception:
            await self._session.rollback()
            self._storage.rollback_replace(staged)
            raise
        self._storage.finish_replace(staged)

    async def _read_verified_file(
        self,
        vault_id: str,
        source_path: str,
        row: VaultFile,
        policy: FilePolicy,
    ) -> tuple[bytes, str]:
        """Return canonical content bytes and its hash, guarding FS/DB split-brain.

        Disk content is served when it matches the recorded content hash. On
        mismatch, a markdown file falls back to its latest stored version so
        callers never receive content inconsistent with a hash. Attachments
        are never versioned (see `_write_revision`), so a hash mismatch on an
        attachment has no fallback and is always an internal error.
        """
        content_bytes = self._read_canonical_file(vault_id, source_path)
        if sha256_bytes(content_bytes) == row.content_hash:
            return content_bytes, row.content_hash
        _LOGGER.warning(
            'canonical file hash mismatch; falling back to stored version '
            '(vault_id=%s path=%s revision=%s)',
            vault_id,
            source_path,
            row.revision,
        )
        if policy.kind is not FileKind.MARKDOWN:
            raise AppError(
                ErrorCode.INTERNAL_ERROR,
                'Canonical attachment does not match its recorded hash and '
                'attachments have no stored version to fall back to.',
                status_code=500,
                details={'vault_id': vault_id, 'path': source_path},
            )
        version = await self._repo.get_version(
            vault_id=vault_id,
            source_path=source_path,
            revision=None,
        )
        if version is None:
            raise AppError(
                ErrorCode.INTERNAL_ERROR,
                'Canonical file does not match its recorded hash and no '
                'stored version is available.',
                status_code=500,
                details={'vault_id': vault_id, 'path': source_path},
            )
        return version.content.encode('utf-8'), version.content_hash

    def _read_canonical_file(self, vault_id: str, source_path: str) -> bytes:
        path = self._storage.vault_path(vault_id, source_path)
        try:
            return path.read_bytes()
        except OSError as exc:
            raise AppError(
                ErrorCode.INTERNAL_ERROR,
                'Canonical vault file could not be read.',
                status_code=500,
                details={'vault_id': vault_id, 'path': source_path},
            ) from exc

    def _validate_writable_path(self, source_path: str) -> FilePolicy:
        if is_ignored_path(source_path):
            _raise_validation(
                'source path is inside an ignored directory',
                {'path': source_path},
            )
        try:
            return classify_file(source_path)
        except DomainValidationError as exc:
            # Fail-closed: any extension outside domain.files' allow-list
            # (unknown or explicitly excluded) is rejected the same way,
            # matching the existing "Only Markdown" behavior's status/code
            # for callers that only inspect the response envelope.
            raise AppError(
                ErrorCode.UNSUPPORTED_FILE_TYPE,
                'File type is not supported by revision sync.',
                status_code=400,
                details=exc.details or {'path': source_path},
            ) from exc

    def _validate_content_size(
        self,
        source_path: str,
        size_bytes: int,
        policy: FilePolicy,
    ) -> None:
        # Markdown keeps its existing settings-driven limit unchanged;
        # attachments use domain.files' per-kind limits (images/PDFs).
        max_bytes = (
            self._settings.sync_max_content_bytes
            if policy.kind is FileKind.MARKDOWN
            else policy.max_bytes
        )
        if size_bytes > max_bytes:
            _raise_validation(
                'content exceeds the maximum allowed size',
                {
                    'path': source_path,
                    'size_bytes': size_bytes,
                    'max_bytes': max_bytes,
                },
            )

    async def _require_vault(self, vault_id: str) -> Vault:
        vault = await self._repo.get_vault(vault_id.strip())
        if vault is None:
            raise AppError(
                ErrorCode.NOT_FOUND,
                'Vault was not found.',
                status_code=404,
                details={'vault_id': vault_id},
            )
        return vault


def _decode_request_content(request: PutFileRequest) -> bytes:
    if request.encoding != 'base64':
        return request.content.encode('utf-8')
    try:
        return base64.b64decode(request.content, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            'content is not valid base64',
            status_code=400,
            details={'encoding': request.encoding},
        ) from exc


def _encode_response_content(
    content_bytes: bytes,
    policy: FilePolicy,
) -> tuple[str, ContentEncoding]:
    if policy.kind is FileKind.MARKDOWN:
        return content_bytes.decode('utf-8'), 'utf8'
    return base64.b64encode(content_bytes).decode('ascii'), 'base64'


def _normalize_path(raw_path: str) -> str:
    try:
        return normalize_source_path(raw_path)
    except DomainValidationError as exc:
        _raise_validation(str(exc), exc.details)


def _verify_hash(content: bytes, raw_hash: str) -> str:
    try:
        return verify_sha256(content, raw_hash)
    except DomainValidationError as exc:
        _raise_validation(str(exc), exc.details)


def _raise_validation(
    message: str,
    details: dict[str, object] | None = None,
) -> NoReturn:
    raise AppError(
        ErrorCode.VALIDATION_ERROR,
        message,
        status_code=400,
        details=details,
    )


def _raise_file_not_found(vault_id: str, source_path: str) -> NoReturn:
    raise AppError(
        ErrorCode.NOT_FOUND,
        'File was not found.',
        status_code=404,
        details={'vault_id': vault_id, 'path': source_path},
    )
