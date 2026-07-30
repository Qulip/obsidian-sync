from typing import Annotated, cast

from fastapi import Depends, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from obsidian_sync.core.auth import AuthContext, require_bearer_token
from obsidian_sync.core.config import Settings, get_settings
from obsidian_sync.db.session import get_db_session
from obsidian_sync.services.post_sync_indexing import PostSyncIndexDispatcher


class RequestMetadata(BaseModel):
    client_ip: str | None
    user_agent: str | None
    token_id: str
    allow_overwrite: bool = False


SettingsDependency = Annotated[Settings, Depends(get_settings)]
AuthDependency = Annotated[AuthContext, Depends(require_bearer_token)]
DbSessionDependency = Annotated[AsyncSession, Depends(get_db_session)]


def get_post_sync_indexer(request: Request) -> PostSyncIndexDispatcher | None:
    return cast(
        PostSyncIndexDispatcher | None,
        getattr(request.app.state, 'post_sync_index_dispatcher', None),
    )


PostSyncIndexerDependency = Annotated[
    PostSyncIndexDispatcher | None,
    Depends(get_post_sync_indexer),
]


def get_request_metadata(
    request: Request,
    auth: AuthDependency,
) -> RequestMetadata:
    return RequestMetadata(
        client_ip=request.client.host if request.client else None,
        user_agent=request.headers.get('user-agent'),
        token_id=auth.token_id,
        allow_overwrite=auth.allow_overwrite,
    )


RequestMetadataDependency = Annotated[
    RequestMetadata,
    Depends(get_request_metadata),
]
