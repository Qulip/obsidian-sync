import re
from collections.abc import Sequence
from typing import NoReturn

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from obsidian_sync.core.config import Settings
from obsidian_sync.core.exceptions import AppError, ErrorCode
from obsidian_sync.db.models import Vault, VaultFile
from obsidian_sync.domain.enums import DocumentVisibility
from obsidian_sync.domain.errors import DomainValidationError
from obsidian_sync.domain.files import FileKind, FilePolicy, validate_file_size
from obsidian_sync.domain.hashing import normalize_sha256, sha256_bytes, verify_sha256
from obsidian_sync.domain.paths import normalize_source_path
from obsidian_sync.repositories.vaults import VaultRepository
from obsidian_sync.schemas.mcp import McpSyncFileRequest
from obsidian_sync.schemas.vaults import (
    ArchiveFilesData,
    ArchiveFilesRequest,
    CreateVaultData,
    CreateVaultRequest,
    ListVaultsData,
    ManifestFile,
    SyncFileData,
    SyncFileRequest,
    SyncManifestData,
    SyncManifestRequest,
    VaultData,
)
from obsidian_sync.services.revision_sync import RevisionSyncService
from obsidian_sync.services.storage import StagedReplace, VaultStorage

_VAULT_ID_PATTERN = re.compile(r'^[a-z0-9-]+$')


class VaultSyncService:
    def __init__(
        self,
        session: AsyncSession,
        storage: VaultStorage,
        *,
        archived_by: str,
        settings: Settings,
    ) -> None:
        self._session = session
        self._repo = VaultRepository(session)
        self._storage = storage
        self._archived_by = archived_by
        self._settings = settings

    async def create_vault(self, request: CreateVaultRequest) -> CreateVaultData:
        vault_id = _normalize_vault_id(request.vault_id)
        name = request.name.strip()
        if not name:
            _raise_validation('vault name is required')

        existing = await self._repo.get_vault(vault_id)
        if existing is not None:
            raise AppError(
                ErrorCode.CONFLICT_DETECTED,
                'Vault already exists.',
                status_code=409,
                details={'vault_id': vault_id},
            )

        vault = self._repo.add_vault(
            vault_id=vault_id,
            name=name,
            description=request.description,
            default_visibility=str(request.default_visibility),
        )
        await self._session.flush()
        await self._session.refresh(vault)
        return CreateVaultData(vault=_vault_data(vault))

    async def list_vaults(self) -> ListVaultsData:
        vaults = await self._repo.list_vaults()
        return ListVaultsData(vaults=[_vault_data(vault) for vault in vaults])

    async def sync_manifest(
        self,
        vault_id: str,
        request: SyncManifestRequest,
    ) -> SyncManifestData:
        normalized_vault_id = _normalize_vault_id(vault_id)
        await self._require_vault(normalized_vault_id)

        incoming = _normalize_manifest_files(request.files)
        existing_rows = await self._repo.list_files(normalized_vault_id)
        existing_by_path = {row.source_path: row for row in existing_rows}
        incoming_by_path = {file.path: file for file in incoming}

        conflicts = self._stored_hash_conflicts(
            normalized_vault_id,
            list(existing_by_path.values()),
        )
        if conflicts:
            _raise_conflicts(conflicts)

        need_upload: list[str] = []
        unchanged: list[str] = []
        for file in incoming:
            row = existing_by_path.get(file.path)
            if row is None or row.content_hash != file.hash:
                need_upload.append(file.path)
            else:
                unchanged.append(file.path)

        archived_candidates = [
            path for path in existing_by_path if path not in incoming_by_path
        ]

        return SyncManifestData(
            need_upload=need_upload,
            unchanged=unchanged,
            archived_candidates=archived_candidates,
            conflicts=[],
        )

    async def sync_file(
        self,
        vault_id: str,
        request: SyncFileRequest,
    ) -> SyncFileData:
        normalized_vault_id = _normalize_vault_id(vault_id)
        vault = await self._require_vault(normalized_vault_id)
        source_path = _normalize_source_path(request.path)
        content = request.content.encode('utf-8')
        if len(content) != request.size:
            _raise_validation(
                'file size does not match content bytes',
                {'expected_size': request.size, 'actual_size': len(content)},
            )
        content_hash = _validate_hash(content, request.hash)
        policy = _validate_markdown_file(source_path, request.size)

        existing = await self._repo.get_file(normalized_vault_id, source_path)
        if existing is not None:
            stored_hash = self._storage.file_hash(normalized_vault_id, source_path)
            if stored_hash != existing.content_hash:
                _raise_conflicts(
                    [
                        {
                            'path': source_path,
                            'reason': 'stored_file_hash_mismatch',
                        }
                    ]
                )
            if existing.content_hash == content_hash:
                return SyncFileData(
                    path=source_path,
                    status='skipped',
                    hash=content_hash,
                )

        staged = self._storage.stage_replace(normalized_vault_id, source_path, content)
        if existing is None:
            self._repo.add_file(
                vault=vault,
                source_path=source_path,
                content_hash=content_hash,
                size_bytes=request.size,
                mime_type=request.mime_type,
                file_type=str(policy.kind),
                vectorize=policy.vectorize,
            )
        else:
            self._repo.update_file(
                existing,
                content_hash=content_hash,
                size_bytes=request.size,
                mime_type=request.mime_type,
                file_type=str(policy.kind),
                vectorize=policy.vectorize,
            )
        await self._commit_staged_file(staged, source_path)
        return SyncFileData(path=source_path, status='uploaded', hash=content_hash)

    async def force_sync_file(
        self,
        vault_id: str,
        request: McpSyncFileRequest,
    ) -> SyncFileData:
        """Write markdown content through the MCP one-way sync tool.

        Fails closed: if `path` already exists with different content, this
        raises a 409 CONFLICT_DETECTED error instead of silently overwriting
        it. Pass `request.overwrite=True` to intentionally replace the
        existing content. Either way, once a write is decided, it delegates
        to `RevisionSyncService` so the vault revision is bumped, a
        `sync_events` row is recorded, and version history is kept -- the
        same invariants the bidirectional (`base_revision`) sync API
        guarantees, so those clients observe the change on their next pull.
        """
        normalized_vault_id = _normalize_vault_id(vault_id)
        await self._require_vault(normalized_vault_id)
        source_path = _normalize_source_path(request.path)
        content_bytes = request.content.encode('utf-8')
        size = len(content_bytes)

        _validate_markdown_file(source_path, size)
        content_hash = sha256_bytes(content_bytes)

        existing = await self._repo.get_file(normalized_vault_id, source_path)
        if existing is not None and not existing.deleted:
            stored_hash = self._storage.file_hash(normalized_vault_id, source_path)
            if stored_hash != existing.content_hash:
                _raise_conflicts(
                    [{'path': source_path, 'reason': 'stored_file_hash_mismatch'}]
                )
            if existing.content_hash == content_hash:
                return SyncFileData(
                    path=source_path,
                    status='skipped',
                    hash=content_hash,
                )
            if not request.overwrite:
                _raise_conflicts(
                    [{'path': source_path, 'reason': 'file_exists_content_differs'}]
                )

        revision_service = RevisionSyncService(
            self._session,
            self._storage,
            self._settings,
        )
        result = await revision_service.force_put_file(
            normalized_vault_id,
            source_path,
            content=request.content,
            device_id=self._archived_by,
        )
        return SyncFileData(
            path=result.path,
            status='uploaded',
            hash=result.content_hash,
        )

    async def archive_files(
        self,
        vault_id: str,
        request: ArchiveFilesRequest,
    ) -> ArchiveFilesData:
        normalized_vault_id = _normalize_vault_id(vault_id)
        await self._require_vault(normalized_vault_id)
        paths = _normalize_paths(request.paths)
        reason = request.reason.strip()
        if not reason:
            _raise_validation('archive reason is required')

        rows = {
            row.source_path: row
            for row in await self._repo.list_files(normalized_vault_id)
            if row.source_path in paths
        }
        missing = [path for path in paths if path not in rows]
        if missing:
            raise AppError(
                ErrorCode.NOT_FOUND,
                'Archive target file was not found.',
                status_code=404,
                details={'paths': missing},
            )

        conflicts = self._stored_hash_conflicts(
            normalized_vault_id,
            [rows[path] for path in paths],
        )
        if conflicts:
            _raise_conflicts(conflicts)

        moves = self._storage.prepare_archive_moves(normalized_vault_id, paths)
        self._storage.stage_archive_moves(moves)
        try:
            chunks = await self._repo.list_chunks_for_paths(normalized_vault_id, paths)
            for path in paths:
                self._repo.archive_file(
                    rows[path],
                    reason=reason,
                    archived_by=self._archived_by,
                )
            for chunk in chunks:
                self._repo.archive_chunk(
                    chunk,
                    reason=reason,
                    archived_by=self._archived_by,
                )
            await self._repo.delete_chunks(normalized_vault_id, paths)
            await self._repo.delete_files(normalized_vault_id, paths)
            await self._session.commit()
        except Exception:
            await self._session.rollback()
            self._storage.rollback_archive_moves(moves)
            raise

        self._storage.finish_archive_moves(moves)
        return ArchiveFilesData(
            vault_id=normalized_vault_id,
            archived=paths,
            reason=reason,
        )

    async def _require_vault(self, vault_id: str) -> Vault:
        vault = await self._repo.get_vault(vault_id)
        if vault is None:
            raise AppError(
                ErrorCode.NOT_FOUND,
                'Vault was not found.',
                status_code=404,
                details={'vault_id': vault_id},
            )
        return vault

    def _stored_hash_conflicts(
        self,
        vault_id: str,
        rows: Sequence[VaultFile],
    ) -> list[dict[str, str]]:
        conflicts: list[dict[str, str]] = []
        for row in rows:
            stored_hash = self._storage.file_hash(vault_id, row.source_path)
            if stored_hash is None:
                conflicts.append(
                    {'path': row.source_path, 'reason': 'stored_file_missing'}
                )
            elif stored_hash != row.content_hash:
                conflicts.append(
                    {'path': row.source_path, 'reason': 'stored_file_hash_mismatch'}
                )
        return conflicts

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


def _normalize_vault_id(raw_vault_id: str) -> str:
    vault_id = raw_vault_id.strip()
    if not vault_id:
        _raise_validation('vault_id is required')
    if _VAULT_ID_PATTERN.fullmatch(vault_id) is None:
        _raise_validation(
            'vault_id must contain lowercase letters, numbers, and hyphens only'
        )
    return vault_id


def _normalize_source_path(raw_path: str) -> str:
    try:
        return normalize_source_path(raw_path)
    except DomainValidationError as exc:
        _raise_validation(str(exc), exc.details)


def _normalize_manifest_files(files: list[ManifestFile]) -> list[ManifestFile]:
    normalized: list[ManifestFile] = []
    seen: set[str] = set()
    for file in files:
        path = _normalize_source_path(file.path)
        if path in seen:
            _raise_validation('manifest contains duplicate path', {'path': path})
        seen.add(path)
        content_hash = _normalize_hash(file.hash)
        _validate_file_policy(path, file.size)
        normalized.append(file.model_copy(update={'path': path, 'hash': content_hash}))
    return normalized


def _normalize_paths(paths: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for path in paths:
        source_path = _normalize_source_path(path)
        if source_path not in seen:
            seen.add(source_path)
            normalized.append(source_path)
    return normalized


def _normalize_hash(raw_hash: str) -> str:
    try:
        return normalize_sha256(raw_hash)
    except DomainValidationError as exc:
        _raise_validation(str(exc), exc.details)


def _validate_hash(content: bytes, raw_hash: str) -> str:
    try:
        return verify_sha256(content, raw_hash)
    except DomainValidationError as exc:
        _raise_validation(str(exc), exc.details)


def _validate_file_policy(source_path: str, size: int) -> FilePolicy:
    try:
        return validate_file_size(source_path, size)
    except DomainValidationError as exc:
        code = (
            ErrorCode.UNSUPPORTED_FILE_TYPE
            if 'extension' in exc.details
            else ErrorCode.VALIDATION_ERROR
        )
        raise AppError(
            code,
            str(exc),
            status_code=400,
            details=exc.details,
        ) from exc


def _validate_markdown_file(source_path: str, size: int) -> FilePolicy:
    policy = _validate_file_policy(source_path, size)
    if policy.kind is not FileKind.MARKDOWN:
        raise AppError(
            ErrorCode.UNSUPPORTED_FILE_TYPE,
            'Only Markdown file uploads are supported by this endpoint.',
            status_code=400,
            details={'path': source_path},
        )
    return policy


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


def _raise_conflicts(conflicts: list[dict[str, str]]) -> None:
    raise AppError(
        ErrorCode.CONFLICT_DETECTED,
        'Conflict detected. Manual verification required.',
        status_code=409,
        details={'conflicts': conflicts},
    )


def _vault_data(vault: Vault) -> VaultData:
    return VaultData(
        vault_id=vault.vault_id,
        name=vault.name,
        description=vault.description,
        default_visibility=DocumentVisibility(vault.default_visibility),
        is_active=vault.is_active,
        created_at=vault.created_at,
    )
