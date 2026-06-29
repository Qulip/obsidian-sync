from typing import Annotated

from fastapi import Depends, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from obsidian_sync.core.auth import AuthContext, require_bearer_token
from obsidian_sync.core.config import Settings, get_settings
from obsidian_sync.db.session import get_db_session


class RequestMetadata(BaseModel):
    client_ip: str | None
    user_agent: str | None
    token_id: str


SettingsDependency = Annotated[Settings, Depends(get_settings)]
AuthDependency = Annotated[AuthContext, Depends(require_bearer_token)]
DbSessionDependency = Annotated[AsyncSession, Depends(get_db_session)]


def get_request_metadata(
    request: Request,
    auth: AuthDependency,
) -> RequestMetadata:
    return RequestMetadata(
        client_ip=request.client.host if request.client else None,
        user_agent=request.headers.get('user-agent'),
        token_id=auth.token_id,
    )


RequestMetadataDependency = Annotated[
    RequestMetadata,
    Depends(get_request_metadata),
]
