import logging
import re
from typing import Literal, NoReturn

from sqlalchemy.ext.asyncio import AsyncSession

from obsidian_sync.core.config import Settings
from obsidian_sync.core.exceptions import AppError, ErrorCode
from obsidian_sync.db.models import Vault
from obsidian_sync.domain.enums import DocumentVisibility
from obsidian_sync.domain.errors import DomainValidationError
from obsidian_sync.domain.files import FileKind, FilePolicy, validate_file_size
from obsidian_sync.domain.hashing import sha256_bytes
from obsidian_sync.domain.paths import normalize_source_path
from obsidian_sync.repositories.vaults import VaultRepository
from obsidian_sync.schemas.mcp import McpSyncFileRequest
from obsidian_sync.schemas.sync import PutFileRequest
from obsidian_sync.schemas.vaults import (
    CreateVaultData,
    CreateVaultRequest,
    ListVaultsData,
    SyncFileData,
    VaultData,
)
from obsidian_sync.services.post_sync_indexing import (
    NoopPostSyncIndexDispatcher,
    PostSyncIndexDispatcher,
)
from obsidian_sync.services.revision_sync import RevisionSyncService
from obsidian_sync.services.storage import VaultStorage

_VAULT_ID_PATTERN = re.compile(r'^[a-z0-9-]+$')

_LOGGER = logging.getLogger(__name__)

# Origins recorded on `sync_events.origin` for writes made through the MCP
# one-way sync tool, so overwrite=True forced replacements are auditable
# separately from ordinary uploads. Writes through the bidirectional
# (base_revision) revision sync API keep `origin=None`, unchanged.
_ORIGIN_MCP = 'mcp'
_ORIGIN_MCP_OVERWRITE = 'mcp_overwrite'


class VaultSyncService:
    def __init__(
        self,
        session: AsyncSession,
        storage: VaultStorage,
        *,
        archived_by: str,
        settings: Settings,
        allow_overwrite: bool = False,
        post_sync_indexer: PostSyncIndexDispatcher | None = None,
    ) -> None:
        self._session = session
        self._repo = VaultRepository(session)
        self._storage = storage
        self._archived_by = archived_by
        self._settings = settings
        self._allow_overwrite = allow_overwrite
        self._post_sync_index_dispatcher = (
            post_sync_indexer or NoopPostSyncIndexDispatcher()
        )

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

    async def force_sync_file(
        self,
        vault_id: str,
        request: McpSyncFileRequest,
    ) -> SyncFileData:
        """Write markdown content through the MCP one-way sync tool.

        Fails closed: if `path` already exists with different content, this
        raises a 409 CONFLICT_DETECTED error instead of silently overwriting
        it. Pass `request.overwrite=True` to intentionally replace the
        existing content -- this requires a token with overwrite permission
        (`allow_overwrite`), or the call fails with 403 FORBIDDEN. Pass
        `request.base_revision` instead to use strict optimistic-concurrency
        writes (same semantics as the bidirectional sync API) without
        needing that permission. The two options are mutually exclusive.

        Either way, once a write is decided, it delegates to
        `RevisionSyncService` so the vault revision is bumped, a
        `sync_events` row is recorded, and version history is kept -- the
        same invariants the bidirectional (`base_revision`) sync API
        guarantees, so those clients observe the change on their next pull.
        """
        if request.base_revision is not None and request.overwrite:
            _raise_validation(
                'base_revision and overwrite are mutually exclusive; use '
                'base_revision for an optimistic-concurrency write or '
                'overwrite=True for a forced replace, not both'
            )
        if request.overwrite and not self._allow_overwrite:
            raise AppError(
                ErrorCode.FORBIDDEN,
                'This token is not permitted to overwrite existing files. '
                'Use base_revision for an optimistic-concurrency write, or '
                'request a token with overwrite permission.',
                status_code=403,
                details={'vault_id': vault_id, 'path': request.path},
            )

        normalized_vault_id = _normalize_vault_id(vault_id)
        await self._require_vault(normalized_vault_id)
        source_path = _normalize_source_path(request.path)
        content_bytes = request.content.encode('utf-8')
        size = len(content_bytes)

        _validate_markdown_file(source_path, size)
        content_hash = sha256_bytes(content_bytes)

        if request.base_revision is not None:
            return await self._force_sync_file_optimistic(
                normalized_vault_id,
                source_path,
                base_revision=request.base_revision,
                content=request.content,
                content_hash=content_hash,
            )

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

        is_overwrite = existing is not None and not existing.deleted
        # Capture scalar copies of the pre-overwrite revision/hash now.
        # `force_put_file` below re-fetches this same row on `self._session`
        # via `get_file_for_update` and mutates it in place; SQLAlchemy's
        # per-Session identity map would hand back the identical Python
        # object, so reading `existing.revision`/`existing.content_hash`
        # *after* the call would observe the already-mutated new values
        # instead of the previous ones.
        previous_revision = existing.revision if existing is not None else None
        previous_content_hash = existing.content_hash if existing is not None else None

        revision_service = RevisionSyncService(
            self._session,
            self._storage,
            self._settings,
            post_sync_indexer=self._post_sync_index_dispatcher,
        )
        result = await revision_service.force_put_file(
            normalized_vault_id,
            source_path,
            content=request.content,
            device_id=self._archived_by,
            origin=_ORIGIN_MCP_OVERWRITE if is_overwrite else _ORIGIN_MCP,
        )
        if is_overwrite:
            assert previous_revision is not None
            assert previous_content_hash is not None
            _log_overwrite_audit(
                vault_id=normalized_vault_id,
                source_path=source_path,
                token_id=self._archived_by,
                previous_revision=previous_revision,
                previous_content_hash=previous_content_hash,
                new_revision=result.revision,
                new_content_hash=result.content_hash,
            )
        return SyncFileData(
            path=result.path,
            status='uploaded',
            hash=result.content_hash,
        )

    async def _force_sync_file_optimistic(
        self,
        vault_id: str,
        source_path: str,
        *,
        base_revision: int,
        content: str,
        content_hash: str,
    ) -> SyncFileData:
        """Strict optimistic-concurrency write, reusing the revision API.

        Delegates to `RevisionSyncService.put_file` -- the same method the
        bidirectional (`base_revision`) sync API uses -- so this path gets
        identical conflict detection/recording (`sync_conflicts`), event
        logging, and version-history semantics for free instead of
        duplicating them. `device_id` is set to the MCP token's id since
        there is no separate device concept on this one-way tool.
        """
        row_before = await self._repo.get_file(vault_id, source_path)
        revision_before = row_before.revision if row_before is not None else 0

        revision_service = RevisionSyncService(
            self._session,
            self._storage,
            self._settings,
            post_sync_indexer=self._post_sync_index_dispatcher,
        )
        result = await revision_service.put_file(
            vault_id,
            source_path,
            PutFileRequest(
                device_id=self._archived_by,
                base_revision=base_revision,
                content_hash=content_hash,
                content=content,
                encoding='utf8',
            ),
            origin=_ORIGIN_MCP,
        )
        # put_file only bumps the revision when it actually records a
        # CREATE/UPDATE event; an unchanged revision means it was an
        # idempotent replay (same base_revision, identical content).
        status: Literal['uploaded', 'skipped'] = (
            'skipped' if result.revision == revision_before else 'uploaded'
        )
        return SyncFileData(path=result.path, status=status, hash=result.content_hash)

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


def _log_overwrite_audit(
    *,
    vault_id: str,
    source_path: str,
    token_id: str,
    previous_revision: int,
    previous_content_hash: str,
    new_revision: int,
    new_content_hash: str,
) -> None:
    _LOGGER.info(
        'mcp overwrite: vault_id=%s path=%s token_id=%s '
        'previous_revision=%s previous_content_hash=%s '
        'new_revision=%s new_content_hash=%s',
        vault_id,
        source_path,
        token_id,
        previous_revision,
        previous_content_hash,
        new_revision,
        new_content_hash,
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
