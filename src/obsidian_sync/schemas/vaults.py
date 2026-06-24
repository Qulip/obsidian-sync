from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from obsidian_sync.domain.enums import DocumentVisibility


class CreateVaultRequest(BaseModel):
    vault_id: str
    name: str
    description: str | None = None
    default_visibility: DocumentVisibility = DocumentVisibility.PERSONAL


class VaultData(BaseModel):
    vault_id: str
    name: str
    description: str | None
    default_visibility: DocumentVisibility
    is_active: bool
    created_at: datetime


class CreateVaultData(BaseModel):
    vault: VaultData


class ListVaultsData(BaseModel):
    vaults: list[VaultData]


class ManifestFile(BaseModel):
    path: str
    hash: str
    size: int = Field(ge=0)
    updated_at: datetime | None = None
    mime_type: str | None = None


class SyncManifestRequest(BaseModel):
    files: list[ManifestFile]


class SyncManifestData(BaseModel):
    need_upload: list[str]
    unchanged: list[str]
    archived_candidates: list[str]
    conflicts: list[dict[str, str]]


class SyncFileRequest(BaseModel):
    path: str
    hash: str
    content: str
    size: int = Field(ge=0)
    mime_type: str | None = 'text/markdown'


class SyncFileData(BaseModel):
    path: str
    status: Literal['uploaded', 'skipped']
    hash: str


class ArchiveFilesRequest(BaseModel):
    paths: list[str]
    reason: str = 'missing_in_manifest'


class ArchiveFilesData(BaseModel):
    vault_id: str
    archived: list[str]
    reason: str
