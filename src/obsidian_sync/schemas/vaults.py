from datetime import datetime
from typing import Literal

from pydantic import BaseModel

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


class SyncFileData(BaseModel):
    path: str
    status: Literal['uploaded', 'skipped']
    hash: str
