import hashlib
import secrets

from fastapi import APIRouter

from obsidian_sync.api.deps import DbSessionDependency
from obsidian_sync.core.exceptions import AppError, ErrorCode
from obsidian_sync.core.responses import ResponseEnvelope, ok
from obsidian_sync.repositories.tokens import TokenRepository
from obsidian_sync.schemas.tokens import (
    TokenCreatedData,
    TokenCreateRequest,
    TokenInfo,
    TokenListData,
)

router = APIRouter(prefix='/tokens', tags=['tokens'])

_TOKEN_PREFIX = 'osk_'


@router.post('', response_model=ResponseEnvelope[TokenCreatedData], status_code=201)
async def create_token(
    body: TokenCreateRequest,
    db: DbSessionDependency,
) -> ResponseEnvelope[TokenCreatedData]:
    raw_token = _TOKEN_PREFIX + secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

    db_token = await TokenRepository(db).create(
        name=body.name,
        token_hash=token_hash,
        expires_at=body.expires_at,
    )
    return ok(
        TokenCreatedData(
            id=db_token.id,
            name=db_token.name,
            token=raw_token,
            expires_at=db_token.expires_at,
            created_at=db_token.created_at,
        )
    )


@router.get('', response_model=ResponseEnvelope[TokenListData])
async def list_tokens(db: DbSessionDependency) -> ResponseEnvelope[TokenListData]:
    tokens = await TokenRepository(db).list_all()
    return ok(
        TokenListData(
            tokens=[
                TokenInfo(
                    id=t.id,
                    name=t.name,
                    is_active=t.is_active,
                    expires_at=t.expires_at,
                    last_used_at=t.last_used_at,
                    created_at=t.created_at,
                )
                for t in tokens
            ]
        )
    )


@router.delete('/{token_id}', response_model=ResponseEnvelope[None])
async def revoke_token(
    token_id: int,
    db: DbSessionDependency,
) -> ResponseEnvelope[None]:
    revoked = await TokenRepository(db).revoke(token_id)
    if not revoked:
        raise AppError(
            ErrorCode.NOT_FOUND,
            'Token not found or already revoked.',
            status_code=404,
        )
    return ok(None)
