from datetime import datetime

from pydantic import BaseModel, Field


class RegisterDeviceRequest(BaseModel):
    device_id: str
    device_name: str | None = None


class RegisterDeviceData(BaseModel):
    vault_id: str
    device_id: str
    registered: bool


class SyncChangeItem(BaseModel):
    revision: int
    path: str
    event_type: str
    content_hash: str | None
    deleted: bool
    created_at: datetime


class SyncChangesData(BaseModel):
    vault_id: str
    from_cursor: int
    to_cursor: int
    changes: list[SyncChangeItem] = Field(default_factory=list)


class SyncStatusData(BaseModel):
    vault_id: str
    device_id: str | None
    server_revision: int
    device_last_seen_revision: int | None
    pending_changes: int
    open_conflicts: int
    pending_vectorizing_jobs: int


class FileContentData(BaseModel):
    vault_id: str
    path: str
    revision: int
    content_hash: str
    content: str
    deleted: bool


class PutFileRequest(BaseModel):
    device_id: str
    base_revision: int = Field(ge=0)
    content_hash: str
    content: str


class PutFileData(BaseModel):
    vault_id: str
    path: str
    revision: int
    content_hash: str


class DeleteFileRequest(BaseModel):
    device_id: str
    base_revision: int = Field(ge=0)


class DeleteFileData(BaseModel):
    vault_id: str
    path: str
    revision: int
    deleted: bool


class RestoreFileRequest(BaseModel):
    path: str
    device_id: str
    restore_revision: int | None = Field(default=None, ge=1)


class McpGetNoteRequest(BaseModel):
    vault_id: str
    path: str


class McpSyncStatusData(BaseModel):
    vault_id: str
    server_revision: int
    open_conflicts: int
    pending_vectorizing_jobs: int
