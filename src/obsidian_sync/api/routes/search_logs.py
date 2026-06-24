from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query

from obsidian_sync.api.deps import DbSessionDependency
from obsidian_sync.core.responses import ResponseEnvelope, ok
from obsidian_sync.repositories.search import SearchRepository
from obsidian_sync.schemas.search import SearchLogsResponse
from obsidian_sync.services.search import SearchLogService

router = APIRouter(prefix='/search-logs', tags=['search-logs'])


@router.get('', response_model=ResponseEnvelope[SearchLogsResponse])
async def list_search_logs(
    session: DbSessionDependency,
    vault_id: str | None = None,
    from_time: Annotated[datetime | None, Query(alias='from')] = None,
    to_time: Annotated[datetime | None, Query(alias='to')] = None,
    query: str | None = None,
    limit: int = 100,
) -> ResponseEnvelope[SearchLogsResponse]:
    service = SearchLogService(
        repository=SearchRepository(session),
    )
    return ok(
        await service.list_logs(
            vault_id=vault_id,
            from_time=from_time,
            to_time=to_time,
            query=query,
            limit=limit,
        )
    )
