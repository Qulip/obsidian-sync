from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from obsidian_sync.db.base import DB_SCHEMA
from obsidian_sync.db.models import SearchLog, Vault
from obsidian_sync.domain.search import SearchFilters


@dataclass(frozen=True, slots=True)
class SearchResultRecord:
    score: float
    source_path: str
    title: str | None
    heading_path: list[str] | None
    document_type: str | None
    project: str | None
    domain: str | None
    priority: str
    status: str
    visibility: str
    tags: list[str] | None
    content: str
    agent_hint: str | None


@dataclass(frozen=True, slots=True)
class SearchLogWrite:
    request_id: str
    token_id: str | None
    vault_pk: int | None
    vault_id: str
    client_ip: str | None
    user_agent: str | None
    query: str
    filters: dict[str, Any]
    top_k: int
    result_count: int
    latency_ms: int


class SearchRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_vault_pk(self, vault_id: str) -> int | None:
        result = await self.session.execute(
            select(Vault.id).where(
                Vault.vault_id == vault_id,
                Vault.is_active.is_(True),
            )
        )
        return result.scalar_one_or_none()

    async def search_chunks(
        self,
        *,
        vault_id: str,
        query_embedding: list[float],
        filters: SearchFilters,
        top_k: int,
    ) -> list[SearchResultRecord]:
        where_parts = [
            'vault_id = :vault_id',
            'embedding IS NOT NULL',
        ]
        params: dict[str, Any] = {
            'vault_id': vault_id,
            'embedding': _vector_literal(query_embedding),
            'top_k': top_k,
        }
        if filters.status:
            where_parts.append('status = ANY(:status)')
            params['status'] = [str(value) for value in filters.status]
        if filters.types:
            where_parts.append('type = ANY(:types)')
            params['types'] = [str(value) for value in filters.types]
        if filters.priority:
            where_parts.append('priority = ANY(:priority)')
            params['priority'] = [str(value) for value in filters.priority]
        if filters.visibility:
            where_parts.append('visibility = ANY(:visibility)')
            params['visibility'] = [str(value) for value in filters.visibility]
        if filters.project:
            where_parts.append('project = :project')
            params['project'] = filters.project
        if filters.domain:
            where_parts.append('domain = :domain')
            params['domain'] = filters.domain
        if filters.tags:
            where_parts.append('tags && CAST(:tags AS text[])')
            params['tags'] = list(filters.tags)

        query = f"""
            SELECT
                1 - (embedding <=> CAST(:embedding AS vector)) AS score,
                source_path,
                title,
                heading_path,
                type AS document_type,
                project,
                domain,
                priority,
                status,
                visibility,
                tags,
                content,
                agent_hint
            FROM {DB_SCHEMA}.knowledge_chunks
            WHERE {' AND '.join(where_parts)}
            ORDER BY embedding <=> CAST(:embedding AS vector)
            LIMIT :top_k
            """  # nosec B608
        statement = text(query)
        result = await self.session.execute(statement, params)
        records: list[SearchResultRecord] = []
        for row in result.mappings():
            records.append(
                SearchResultRecord(
                    score=float(row['score']),
                    source_path=str(row['source_path']),
                    title=row['title'],
                    heading_path=row['heading_path'],
                    document_type=row['document_type'],
                    project=row['project'],
                    domain=row['domain'],
                    priority=str(row['priority']),
                    status=str(row['status']),
                    visibility=str(row['visibility']),
                    tags=row['tags'],
                    content=str(row['content']),
                    agent_hint=row['agent_hint'],
                )
            )
        return records

    async def log_search(self, log: SearchLogWrite) -> None:
        self.session.add(
            SearchLog(
                request_id=log.request_id,
                token_id=log.token_id,
                vault_pk=log.vault_pk,
                vault_id=log.vault_id,
                client_ip=log.client_ip,
                user_agent=log.user_agent,
                query=log.query,
                filters=log.filters,
                top_k=log.top_k,
                result_count=log.result_count,
                latency_ms=log.latency_ms,
                created_at=datetime.now(timezone(timedelta(hours=9))).replace(
                    tzinfo=None
                ),
            )
        )

    async def list_logs(
        self,
        *,
        vault_id: str | None,
        from_time: datetime | None,
        to_time: datetime | None,
        query: str | None,
        limit: int,
    ) -> list[SearchLog]:
        statement = select(SearchLog)
        if vault_id:
            statement = statement.where(SearchLog.vault_id == vault_id)
        if from_time:
            statement = statement.where(SearchLog.created_at >= from_time)
        if to_time:
            statement = statement.where(SearchLog.created_at <= to_time)
        if query:
            statement = statement.where(SearchLog.query.ilike(f'%{query}%'))
        result = await self.session.execute(
            statement.order_by(SearchLog.created_at.desc()).limit(limit)
        )
        return list(result.scalars())


def _vector_literal(values: list[float]) -> str:
    return '[' + ','.join(str(value) for value in values) + ']'
