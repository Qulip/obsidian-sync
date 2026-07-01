from datetime import UTC, datetime

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from obsidian_sync.db.models import ApiToken


class TokenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_by_hash(self, token_hash: str) -> ApiToken | None:
        now = _utcnow_naive()
        result = await self._session.execute(
            select(ApiToken).where(
                ApiToken.token_hash == token_hash,
                ApiToken.is_active.is_(True),
                or_(ApiToken.expires_at.is_(None), ApiToken.expires_at > now),
            )
        )
        return result.scalar_one_or_none()

    async def touch_last_used(self, token_id: int) -> None:
        await self._session.execute(
            update(ApiToken)
            .where(ApiToken.id == token_id)
            .values(last_used_at=_utcnow_naive())
        )

    async def create(
        self,
        name: str,
        token_hash: str,
        expires_at: datetime | None,
    ) -> ApiToken:
        token = ApiToken(name=name, token_hash=token_hash, expires_at=expires_at)
        self._session.add(token)
        await self._session.flush()
        await self._session.refresh(token)
        return token

    async def list_all(self) -> list[ApiToken]:
        result = await self._session.execute(
            select(ApiToken).order_by(ApiToken.created_at.desc())
        )
        return list(result.scalars().all())

    async def revoke(self, token_id: int) -> bool:
        result = await self._session.execute(
            update(ApiToken)
            .where(ApiToken.id == token_id, ApiToken.is_active.is_(True))
            .values(is_active=False)
            .returning(ApiToken.id)
        )
        return result.scalar_one_or_none() is not None


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)
