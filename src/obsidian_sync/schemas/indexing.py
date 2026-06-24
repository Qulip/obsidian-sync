from enum import StrEnum

from pydantic import BaseModel, Field


class ReindexMode(StrEnum):
    CHANGED_ONLY = 'changed_only'
    FULL = 'full'


class ReindexVaultRequest(BaseModel):
    mode: ReindexMode = ReindexMode.CHANGED_ONLY


class ReindexFileRequest(BaseModel):
    path: str
    content: str | None = None


class ReindexResult(BaseModel):
    vault_id: str
    mode: str
    indexed_files: int = 0
    skipped_files: int = 0
    failed_files: int = 0
    created_chunks: int = 0
    updated_chunks: int = 0
    deleted_chunks: int = 0
    failures: list[str] = Field(default_factory=list)
