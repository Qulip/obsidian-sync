import hashlib
import secrets
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from obsidian_sync.core.config import Settings, get_settings
from obsidian_sync.core.exceptions import AppError, ErrorCode

bearer_scheme = HTTPBearer(auto_error=False)


class AuthContext(BaseModel):
    token_id: str


def token_identifier(token: str) -> str:
    return hashlib.sha256(token.encode('utf-8')).hexdigest()[:12]


async def require_bearer_token(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(bearer_scheme),
    ],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AuthContext:
    if credentials is None or credentials.scheme.lower() != 'bearer':
        raise AppError(
            ErrorCode.UNAUTHORIZED,
            'Bearer token is required.',
            status_code=401,
        )

    configured_token = (
        settings.api_token.get_secret_value() if settings.api_token else None
    )
    if not configured_token:
        raise AppError(
            ErrorCode.INTERNAL_ERROR,
            'API token is not configured.',
            status_code=500,
        )

    if not secrets.compare_digest(credentials.credentials, configured_token):
        raise AppError(
            ErrorCode.UNAUTHORIZED,
            'Invalid bearer token.',
            status_code=401,
        )

    return AuthContext(token_id=token_identifier(configured_token))
