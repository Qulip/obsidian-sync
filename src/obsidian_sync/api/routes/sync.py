from typing import Annotated

from fastapi import APIRouter, Query

from obsidian_sync.api.deps import DbSessionDependency, SettingsDependency
from obsidian_sync.core.responses import ResponseEnvelope, ok
from obsidian_sync.schemas.sync import (
    DeleteFileData,
    DeleteFileRequest,
    FileContentData,
    PutFileData,
    PutFileRequest,
    RegisterDeviceData,
    RegisterDeviceRequest,
    RestoreFileRequest,
    SyncChangesData,
    SyncStatusData,
)
from obsidian_sync.services.revision_sync import RevisionSyncService
from obsidian_sync.services.storage import VaultStorage

router = APIRouter(prefix='/vaults', tags=['sync'])

MAX_CHANGES_LIMIT = 1000


@router.post(
    '/{vault_id}/sync/devices',
    response_model=ResponseEnvelope[RegisterDeviceData],
)
async def register_device(
    vault_id: str,
    payload: RegisterDeviceRequest,
    session: DbSessionDependency,
    settings: SettingsDependency,
) -> ResponseEnvelope[RegisterDeviceData]:
    """Register or refresh a sync device for a vault."""
    service = _service(session=session, settings=settings)
    return ok(await service.register_device(vault_id, payload))


@router.get(
    '/{vault_id}/sync/changes',
    response_model=ResponseEnvelope[SyncChangesData],
)
async def list_changes(
    vault_id: str,
    session: DbSessionDependency,
    settings: SettingsDependency,
    since: Annotated[int, Query(ge=0)] = 0,
    device_id: Annotated[str | None, Query()] = None,
    limit: Annotated[int | None, Query(ge=1, le=MAX_CHANGES_LIMIT)] = None,
) -> ResponseEnvelope[SyncChangesData]:
    """List sync events after the given vault-global revision cursor."""
    service = _service(session=session, settings=settings)
    return ok(
        await service.list_changes(
            vault_id,
            since=since,
            device_id=device_id,
            limit=limit or settings.sync_changes_default_limit,
        )
    )


@router.get(
    '/{vault_id}/sync/status',
    response_model=ResponseEnvelope[SyncStatusData],
)
async def sync_status(
    vault_id: str,
    session: DbSessionDependency,
    settings: SettingsDependency,
    device_id: Annotated[str | None, Query()] = None,
) -> ResponseEnvelope[SyncStatusData]:
    """Report server revision, pending changes, and open conflicts."""
    service = _service(session=session, settings=settings)
    return ok(await service.get_status(vault_id, device_id=device_id))


@router.post(
    '/{vault_id}/sync/restore',
    response_model=ResponseEnvelope[PutFileData],
)
async def restore_file(
    vault_id: str,
    payload: RestoreFileRequest,
    session: DbSessionDependency,
    settings: SettingsDependency,
) -> ResponseEnvelope[PutFileData]:
    """Restore a soft-deleted file from its stored version history.

    Restore is body-based because a literal segment after a `{file_path:path}`
    parameter does not route reliably.
    """
    service = _service(session=session, settings=settings)
    return ok(await service.restore_file(vault_id, payload))


@router.get(
    '/{vault_id}/files/{file_path:path}',
    response_model=ResponseEnvelope[FileContentData],
)
async def get_file(
    vault_id: str,
    file_path: str,
    session: DbSessionDependency,
    settings: SettingsDependency,
) -> ResponseEnvelope[FileContentData]:
    """Read the latest canonical content and revision of a file."""
    service = _service(session=session, settings=settings)
    return ok(await service.get_file(vault_id, file_path))


@router.put(
    '/{vault_id}/files/{file_path:path}',
    response_model=ResponseEnvelope[PutFileData],
)
async def put_file(
    vault_id: str,
    file_path: str,
    payload: PutFileRequest,
    session: DbSessionDependency,
    settings: SettingsDependency,
) -> ResponseEnvelope[PutFileData]:
    """Create or update a markdown file with optimistic revision checking.

    Use base_revision=0 to create a new file (recreating a soft-deleted path
    is allowed). For updates, base_revision must match the server revision or
    a 409 SYNC_CONFLICT is returned and recorded.
    """
    service = _service(session=session, settings=settings)
    return ok(await service.put_file(vault_id, file_path, payload))


@router.delete(
    '/{vault_id}/files/{file_path:path}',
    response_model=ResponseEnvelope[DeleteFileData],
)
async def delete_file(
    vault_id: str,
    file_path: str,
    payload: DeleteFileRequest,
    session: DbSessionDependency,
    settings: SettingsDependency,
) -> ResponseEnvelope[DeleteFileData]:
    """Soft-delete a file with optimistic revision checking.

    This endpoint requires a JSON body ({device_id, base_revision}); FastAPI
    supports request bodies on DELETE. The canonical file stays on disk and
    all rows are kept; a cleanup job archives expired soft-deleted files.
    """
    service = _service(session=session, settings=settings)
    return ok(await service.delete_file(vault_id, file_path, payload))


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
