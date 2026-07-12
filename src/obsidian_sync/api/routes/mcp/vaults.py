from fastapi import APIRouter

from obsidian_sync.api.deps import (
    DbSessionDependency,
    RequestMetadataDependency,
    SettingsDependency,
)
from obsidian_sync.clients.ollama import OllamaClient
from obsidian_sync.core.responses import ResponseEnvelope, ok
from obsidian_sync.repositories.indexing import IndexingRepository
from obsidian_sync.schemas.indexing import ReindexResult, ReindexVaultRequest
from obsidian_sync.schemas.mcp import McpSyncFileRequest
from obsidian_sync.schemas.vaults import ListVaultsData, SyncFileData
from obsidian_sync.services.indexing import ReindexService
from obsidian_sync.services.storage import VaultStorage
from obsidian_sync.services.vault_sync import VaultSyncService

router = APIRouter(prefix='/mcp/vaults', tags=['mcp'])


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
    '/{vault_id}/sync/file',
    response_model=ResponseEnvelope[SyncFileData],
)
async def sync_file(
    vault_id: str,
    payload: McpSyncFileRequest,
    session: DbSessionDependency,
    settings: SettingsDependency,
    metadata: RequestMetadataDependency,
) -> ResponseEnvelope[SyncFileData]:
    """Step 1 of saving a note: upload markdown content.

    Writes the note content to the personal knowledge base. Fails closed: if
    path already exists with different content, this returns a 409 conflict
    instead of silently overwriting it — pass overwrite=True to explicitly
    replace it. Content must be valid markdown; frontmatter (title, tags,
    date) should be included at the top.

    Save workflow: sync_file → reindex_vault.
    """
    service = _vault_service(session=session, settings=settings, metadata=metadata)
    return ok(await service.force_sync_file(vault_id, payload))


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
    """Step 2 of saving a note: make the new content searchable.

    Re-embeds uploaded files so they appear in future search_knowledge results.
    Use mode='changed_only' after saving a note (faster). Use mode='full' only
    when repairing a broken index.

    Save workflow: sync_file → reindex_vault.
    """
    service = _reindex_service(session=session, settings=settings)
    return ok(await service.reindex_vault(vault_id=vault_id, mode=payload.mode))


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


def _reindex_service(
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
