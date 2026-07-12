from datetime import datetime

from pydantic import BaseModel, Field


class TokenCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    expires_at: datetime | None = None
    allow_overwrite: bool = Field(
        default=False,
        description=(
            'Fail-closed by default: grants this token permission to use '
            'McpSyncFileRequest.overwrite=True on the MCP sync_file tool to '
            'force-replace existing content. Without it, overwrite=True is '
            'rejected with 403 FORBIDDEN; base_revision writes are '
            'unaffected and always allowed.'
        ),
    )


class TokenCreatedData(BaseModel):
    id: int
    name: str
    token: str
    expires_at: datetime | None
    allow_overwrite: bool
    created_at: datetime


class TokenInfo(BaseModel):
    id: int
    name: str
    is_active: bool
    expires_at: datetime | None
    last_used_at: datetime | None
    allow_overwrite: bool
    created_at: datetime


class TokenListData(BaseModel):
    tokens: list[TokenInfo]
