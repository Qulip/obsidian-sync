from datetime import datetime

from pydantic import BaseModel, Field


class TokenCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    expires_at: datetime | None = None


class TokenCreatedData(BaseModel):
    id: int
    name: str
    token: str
    expires_at: datetime | None
    created_at: datetime


class TokenInfo(BaseModel):
    id: int
    name: str
    is_active: bool
    expires_at: datetime | None
    last_used_at: datetime | None
    created_at: datetime


class TokenListData(BaseModel):
    tokens: list[TokenInfo]
