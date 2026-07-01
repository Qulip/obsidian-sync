import hashlib
import secrets
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from obsidian_sync.core.config import Settings, get_settings
from obsidian_sync.core.exceptions import AppError, ErrorCode
from obsidian_sync.db.session import get_db_session
from obsidian_sync.repositories.tokens import TokenRepository

bearer_scheme = HTTPBearer(auto_error=False)
_PREFIX_LEN = 12


class AuthContext(BaseModel):
    token_id: str


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def token_identifier(token: str) -> str:
    return _hash_token(token)[:_PREFIX_LEN]


def _extract_raw_token(credentials: HTTPAuthorizationCredentials | None) -> str:
    if credentials is None or credentials.scheme.lower() != 'bearer':
        raise AppError(
            ErrorCode.UNAUTHORIZED,
            'Bearer token is required.',
            status_code=401,
        )
    return credentials.credentials


async def require_bearer_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> AuthContext:
    raw_token = _extract_raw_token(credentials)
    h = _hash_token(raw_token)
    repo = TokenRepository(db)
    db_token = await repo.find_by_hash(h)
    if db_token is None:
        raise AppError(ErrorCode.UNAUTHORIZED, 'Invalid bearer token.', status_code=401)

    await repo.touch_last_used(db_token.id)
    return AuthContext(token_id=h[:_PREFIX_LEN])


async def require_admin_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AuthContext:
    raw_token = _extract_raw_token(credentials)
    configured_token = (
        settings.api_token.get_secret_value() if settings.api_token else None
    )
    if not configured_token:
        raise AppError(
            ErrorCode.INTERNAL_ERROR,
            'Admin token is not configured.',
            status_code=500,
        )
    if not secrets.compare_digest(raw_token, configured_token):
        raise AppError(
            ErrorCode.UNAUTHORIZED, 'Admin access required.', status_code=401
        )
    return AuthContext(token_id=token_identifier(configured_token))
