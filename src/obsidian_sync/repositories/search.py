from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession

from obsidian_sync.db.base import DB_SCHEMA
from obsidian_sync.db.models import KnowledgeChunk, SearchLog, Vault, VaultFile
from obsidian_sync.domain.search import SearchFilters


@dataclass(frozen=True, slots=True)
class SearchResultRecord:
    chunk_id: int
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
    revision: int | None
    updated_at: datetime | None


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
        embedding_model: str,
        candidate_limit: int | None = None,
    ) -> list[SearchResultRecord]:
        where_parts, params = _base_where_and_params(
            vault_id=vault_id,
            query_embedding=query_embedding,
            filters=filters,
            embedding_model=embedding_model,
        )
        params['limit'] = candidate_limit if candidate_limit is not None else top_k

        query = f"""
            SELECT
                {_SELECT_COLUMNS}
            FROM {DB_SCHEMA}.knowledge_chunks AS kc
            JOIN {DB_SCHEMA}.vault_files AS vf
                ON vf.vault_id = kc.vault_id
                AND vf.source_path = kc.source_path
                AND vf.content_hash = kc.content_hash
            WHERE {' AND '.join(where_parts)}
            ORDER BY kc.embedding <=> CAST(:embedding AS vector)
            LIMIT :limit
            """  # nosec B608
        statement = text(query)
        result = await self.session.execute(statement, params)
        return [_row_to_record(row) for row in result.mappings()]

    async def search_chunks_lexical(
        self,
        *,
        vault_id: str,
        query_embedding: list[float],
        query_text: str,
        filters: SearchFilters,
        candidate_limit: int,
        embedding_model: str,
    ) -> list[SearchResultRecord]:
        where_parts, params = _base_where_and_params(
            vault_id=vault_id,
            query_embedding=query_embedding,
            filters=filters,
            embedding_model=embedding_model,
        )
        where_parts.append(
            "kc.content_tsv @@ websearch_to_tsquery('simple', :query_text)"
        )
        params['query_text'] = query_text
        params['limit'] = candidate_limit

        query = f"""
            SELECT
                {_SELECT_COLUMNS}
            FROM {DB_SCHEMA}.knowledge_chunks AS kc
            JOIN {DB_SCHEMA}.vault_files AS vf
                ON vf.vault_id = kc.vault_id
                AND vf.source_path = kc.source_path
                AND vf.content_hash = kc.content_hash
            WHERE {' AND '.join(where_parts)}
            ORDER BY ts_rank(
                kc.content_tsv, websearch_to_tsquery('simple', :query_text)
            ) DESC
            LIMIT :limit
            """  # nosec B608
        statement = text(query)
        result = await self.session.execute(statement, params)
        return [_row_to_record(row) for row in result.mappings()]

    async def count_pending_reindex(self, vault_id: str) -> int:
        """Count vault files awaiting reindexing, excluded from search results.

        Mirrors SyncRepository.count_pending_vectorizing's condition so the
        "pending" definition stays consistent across sync status and search
        freshness reporting.
        """
        result = await self.session.execute(
            select(func.count())
            .select_from(VaultFile)
            .where(
                VaultFile.vault_id == vault_id,
                VaultFile.index_status == 'pending',
                VaultFile.vectorize.is_(True),
                VaultFile.deleted.is_(False),
            )
        )
        return int(result.scalar_one())

    async def count_failed_reindex(self, vault_id: str) -> int:
        """Count vault files whose reindex failed, excluded from search results.

        Same shape as count_pending_reindex but for index_status='failed' --
        files ReindexService could not vectorize after an error, which are
        also missing from search results and must not be reported as fresh.
        """
        result = await self.session.execute(
            select(func.count())
            .select_from(VaultFile)
            .where(
                VaultFile.vault_id == vault_id,
                VaultFile.index_status == 'failed',
                VaultFile.vectorize.is_(True),
                VaultFile.deleted.is_(False),
            )
        )
        return int(result.scalar_one())

    async def count_model_stale_files(
        self, vault_id: str, embedding_model: str
    ) -> int:
        """Count indexed files whose current chunks used a different model.

        A file is "model stale" when it is indexed (index_status='indexed',
        deleted=false, vectorize=true) but the knowledge_chunks row matching
        its current content_hash was embedded with a model other than
        `embedding_model` -- e.g. right after an embedding model
        configuration change and before a full reindex has completed. Those
        chunks are excluded from search results by _base_where_and_params'
        `kc.embedding_model = :embedding_model` filter, so this count
        mirrors count_pending_reindex/count_failed_reindex to keep search
        freshness reporting consistent.
        """
        stale_chunk_exists = (
            select(KnowledgeChunk.id)
            .where(
                KnowledgeChunk.vault_id == VaultFile.vault_id,
                KnowledgeChunk.source_path == VaultFile.source_path,
                KnowledgeChunk.content_hash == VaultFile.content_hash,
                KnowledgeChunk.embedding_model != embedding_model,
            )
            .correlate(VaultFile)
            .exists()
        )
        result = await self.session.execute(
            select(func.count(func.distinct(VaultFile.id)))
            .select_from(VaultFile)
            .where(
                VaultFile.vault_id == vault_id,
                VaultFile.index_status == 'indexed',
                VaultFile.vectorize.is_(True),
                VaultFile.deleted.is_(False),
                stale_chunk_exists,
            )
        )
        return int(result.scalar_one())

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

    async def record_feedback(
        self,
        *,
        request_id: str,
        vault_id: str,
        helpful: bool | None,
        selected_source_path: str | None,
        selected_chunk_rank: int | None,
        expected_missing: bool | None,
        comment: str | None,
    ) -> SearchLog | None:
        result = await self.session.execute(
            select(SearchLog).where(
                SearchLog.request_id == request_id,
                SearchLog.vault_id == vault_id,
            )
        )
        log = result.scalar_one_or_none()
        if log is None:
            return None
        log.feedback_helpful = helpful
        log.feedback_selected_source_path = selected_source_path
        log.feedback_selected_chunk_rank = selected_chunk_rank
        log.feedback_expected_missing = expected_missing
        log.feedback_comment = comment
        log.feedback_at = datetime.now(timezone(timedelta(hours=9))).replace(
            tzinfo=None
        )
        await self.session.flush()
        return log

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


_SELECT_COLUMNS = """
                kc.id AS chunk_id,
                1 - (kc.embedding <=> CAST(:embedding AS vector)) AS score,
                kc.source_path,
                kc.title,
                kc.heading_path,
                kc.type AS document_type,
                kc.project,
                kc.domain,
                kc.priority,
                kc.status,
                kc.visibility,
                kc.tags,
                kc.content,
                kc.agent_hint,
                vf.revision AS file_revision,
                vf.updated_at AS file_updated_at""".strip()


def _base_where_and_params(
    *,
    vault_id: str,
    query_embedding: list[float],
    filters: SearchFilters,
    embedding_model: str,
) -> tuple[list[str], dict[str, Any]]:
    where_parts = [
        'kc.vault_id = :vault_id',
        'kc.embedding IS NOT NULL',
        'kc.embedding_model = :embedding_model',
        'vf.deleted = false',
        "vf.index_status = 'indexed'",
    ]
    params: dict[str, Any] = {
        'vault_id': vault_id,
        'embedding': _vector_literal(query_embedding),
        'embedding_model': embedding_model,
    }
    if filters.status:
        where_parts.append('kc.status = ANY(:status)')
        params['status'] = [str(value) for value in filters.status]
    if filters.types:
        where_parts.append('kc.type = ANY(:types)')
        params['types'] = [str(value) for value in filters.types]
    if filters.priority:
        where_parts.append('kc.priority = ANY(:priority)')
        params['priority'] = [str(value) for value in filters.priority]
    if filters.visibility:
        where_parts.append('kc.visibility = ANY(:visibility)')
        params['visibility'] = [str(value) for value in filters.visibility]
    if filters.project:
        where_parts.append('kc.project = :project')
        params['project'] = filters.project
    if filters.domain:
        where_parts.append('kc.domain = :domain')
        params['domain'] = filters.domain
    if filters.tags:
        where_parts.append('kc.tags && CAST(:tags AS text[])')
        params['tags'] = list(filters.tags)
    return where_parts, params


def _row_to_record(row: RowMapping) -> SearchResultRecord:
    return SearchResultRecord(
        chunk_id=int(row['chunk_id']),
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
        revision=(
            int(row['file_revision']) if row['file_revision'] is not None else None
        ),
        updated_at=row['file_updated_at'],
    )
