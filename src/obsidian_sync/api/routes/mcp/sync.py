from fastapi import APIRouter

from obsidian_sync.api.deps import DbSessionDependency, SettingsDependency
from obsidian_sync.core.responses import ResponseEnvelope, ok
from obsidian_sync.schemas.sync import (
    FileContentData,
    McpGetNoteRequest,
    McpSyncStatusData,
)
from obsidian_sync.services.revision_sync import RevisionSyncService
from obsidian_sync.services.storage import VaultStorage

router = APIRouter(prefix='/mcp/vaults', tags=['mcp'])


@router.post('/note', response_model=ResponseEnvelope[FileContentData])
async def get_note(
    payload: McpGetNoteRequest,
    session: DbSessionDependency,
    settings: SettingsDependency,
) -> ResponseEnvelope[FileContentData]:
    """Read the latest content of a note with its revision and content hash.

    Use the vault_id from list_vaults and the source_path from search results
    to fetch the full note body when a search snippet is not enough.
    """
    service = _service(session=session, settings=settings)
    return ok(await service.get_file(payload.vault_id, payload.path))


@router.get(
    '/{vault_id}/sync/status',
    response_model=ResponseEnvelope[McpSyncStatusData],
)
async def get_sync_status(
    vault_id: str,
    session: DbSessionDependency,
    settings: SettingsDependency,
) -> ResponseEnvelope[McpSyncStatusData]:
    """Report the vault's server revision, open conflicts, and pending jobs."""
    service = _service(session=session, settings=settings)
    status = await service.get_status(vault_id, device_id=None)
    return ok(
        McpSyncStatusData(
            vault_id=status.vault_id,
            server_revision=status.server_revision,
            open_conflicts=status.open_conflicts,
            pending_vectorizing_jobs=status.pending_vectorizing_jobs,
        )
    )


def _service(
    *,
    session: DbSessionDependency,
    settings: SettingsDependency,
) -> RevisionSyncService:
    return RevisionSyncService(
        session,
        VaultStorage(settings.vault_storage_root, settings.vault_archive_root),
        settings,
    )
