from typing import NoReturn

from fastapi import APIRouter

from obsidian_sync.api.deps import (
    DbSessionDependency,
    RequestMetadataDependency,
    SettingsDependency,
)
from obsidian_sync.clients.ollama import OllamaClient
from obsidian_sync.core.exceptions import AppError, ErrorCode
from obsidian_sync.core.responses import ResponseEnvelope, ok
from obsidian_sync.repositories.indexing import IndexingRepository
from obsidian_sync.schemas.indexing import (
    ReindexFileRequest,
    ReindexResult,
    ReindexVaultRequest,
)
from obsidian_sync.schemas.vaults import (
    CreateVaultData,
    CreateVaultRequest,
    ListVaultsData,
)
from obsidian_sync.services.indexing import ReindexService
from obsidian_sync.services.storage import VaultStorage
from obsidian_sync.services.vault_sync import VaultSyncService

router = APIRouter(prefix='/vaults', tags=['vaults'])


def _not_implemented() -> NoReturn:
    raise AppError(
        ErrorCode.INTERNAL_ERROR,
        'Endpoint is reserved for a later implementation slice.',
        status_code=501,
    )


@router.post('', response_model=ResponseEnvelope[CreateVaultData])
async def create_vault(
    payload: CreateVaultRequest,
    session: DbSessionDependency,
    settings: SettingsDependency,
    metadata: RequestMetadataDependency,
) -> ResponseEnvelope[CreateVaultData]:
    service = _vault_service(session=session, settings=settings, metadata=metadata)
    return ok(await service.create_vault(payload))


@router.get('', response_model=ResponseEnvelope[ListVaultsData])
async def list_vaults(
    session: DbSessionDependency,
    settings: SettingsDependency,
    metadata: RequestMetadataDependency,
) -> ResponseEnvelope[ListVaultsData]:
    """List all available personal knowledge bases.

    Call this first to discover vault IDs and their topics before searching or
    saving notes. Each vault represents a separate knowledge domain (e.g. work,
    personal, a specific project). Use the vault_id from this response in
    subsequent search or save calls.
    """
    service = _vault_service(session=session, settings=settings, metadata=metadata)
    return ok(await service.list_vaults())


@router.post(
    '/{vault_id}/reindex',
    response_model=ResponseEnvelope[ReindexResult],
)
async def reindex_vault(
    vault_id: str,
    payload: ReindexVaultRequest,
    session: DbSessionDependency,
    settings: SettingsDependency,
) -> ResponseEnvelope[ReindexResult]:
    """Make saved content searchable.

    Re-embeds uploaded files so they appear in future search_knowledge results.
    Use mode='changed_only' after saving a note (faster). Use mode='full' only
    when repairing a broken index.

    Save workflow: PUT /vaults/{vault_id}/files/{path} (or the MCP sync_file
    tool) → reindex_vault.
    """
    service = _service(session=session, settings=settings)
    return ok(await service.reindex_vault(vault_id=vault_id, mode=payload.mode))


@router.post(
    '/{vault_id}/reindex/file',
    response_model=ResponseEnvelope[ReindexResult],
)
async def reindex_file(
    vault_id: str,
    payload: ReindexFileRequest,
    session: DbSessionDependency,
    settings: SettingsDependency,
) -> ResponseEnvelope[ReindexResult]:
    service = _service(session=session, settings=settings)
    return ok(
        await service.reindex_file(
            vault_id=vault_id,
            source_path=payload.path,
            content=payload.content,
        )
    )


def _service(
    *,
    session: DbSessionDependency,
    settings: SettingsDependency,
) -> ReindexService:
    return ReindexService(
        repository=IndexingRepository(session),
        ollama_client=OllamaClient(
            base_url=settings.ollama_base_url,
            model=settings.embedding_model,
            timeout_seconds=settings.ollama_timeout_seconds,
        ),
        settings=settings,
    )


def _vault_service(
    *,
    session: DbSessionDependency,
    settings: SettingsDependency,
    metadata: RequestMetadataDependency,
) -> VaultSyncService:
    return VaultSyncService(
        session,
        VaultStorage(settings.vault_storage_root, settings.vault_archive_root),
        archived_by=metadata.token_id,
        settings=settings,
    )
