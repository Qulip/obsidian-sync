from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from obsidian_sync.core.auth import AuthContext, require_bearer_token
from obsidian_sync.core.config import Settings, get_settings
from obsidian_sync.core.exceptions import AppError, ErrorCode


class RequestMetadata(BaseModel):
    client_ip: str | None
    user_agent: str | None
    token_id: str


SettingsDependency = Annotated[Settings, Depends(get_settings)]
AuthDependency = Annotated[AuthContext, Depends(require_bearer_token)]


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


async def get_db_session(request: Request) -> AsyncIterator[AsyncSession]:
    sessionmaker = getattr(request.app.state, 'sessionmaker', None)
    if not isinstance(sessionmaker, async_sessionmaker):
        raise AppError(
            ErrorCode.INTERNAL_ERROR,
            'Database is not configured.',
            status_code=500,
        )

    async with sessionmaker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


DbSessionDependency = Annotated[AsyncSession, Depends(get_db_session)]
