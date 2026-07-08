from datetime import datetime
from time import perf_counter
from typing import Any
from uuid import uuid4

from obsidian_sync.clients.ollama import OllamaClient
from obsidian_sync.core.exceptions import AppError, ErrorCode
from obsidian_sync.domain.errors import DomainValidationError
from obsidian_sync.domain.search import NormalizedSearchQuery, normalize_search_query
from obsidian_sync.repositories.search import SearchLogWrite, SearchRepository
from obsidian_sync.schemas.search import (
    AnswerContext,
    KnowledgeSearchResponse,
    KnowledgeSearchResult,
    SearchLogItem,
    SearchLogsResponse,
)


class KnowledgeSearchService:
    def __init__(
        self,
        *,
        repository: SearchRepository,
        ollama_client: OllamaClient,
    ) -> None:
        self.repository = repository
        self.ollama_client = ollama_client

    async def search(
        self,
        *,
        vault_id: str,
        query: str,
        filters: dict[str, Any] | None,
        top_k: int | None,
        project: str | None,
        domain: str | None,
        token_id: str | None,
        client_ip: str | None,
        user_agent: str | None,
    ) -> KnowledgeSearchResponse:
        normalized = _normalize_or_raise(
            vault_id=vault_id,
            query=query,
            filters=filters,
            top_k=top_k,
            project=project,
            domain=domain,
        )
        started = perf_counter()
        request_id = uuid4().hex
        vault_pk = await self.repository.get_vault_pk(normalized.vault_id)
        if vault_pk is None:
            raise AppError(ErrorCode.NOT_FOUND, 'Vault was not found.', status_code=404)

        query_embedding = await self.ollama_client.embed(normalized.query)
        records = await self.repository.search_chunks(
            vault_id=normalized.vault_id,
            query_embedding=query_embedding,
            filters=normalized.filters,
            top_k=normalized.top_k,
        )
        latency_ms = int((perf_counter() - started) * 1000)
        filter_payload = _filters_payload(normalized)
        await self.repository.log_search(
            SearchLogWrite(
                request_id=request_id,
                token_id=token_id,
                vault_pk=vault_pk,
                vault_id=normalized.vault_id,
                client_ip=client_ip,
                user_agent=user_agent,
                query=normalized.query,
                filters=filter_payload,
                top_k=normalized.top_k,
                result_count=len(records),
                latency_ms=latency_ms,
            )
        )

        return KnowledgeSearchResponse(
            query=normalized.query,
            vault_id=normalized.vault_id,
            project=normalized.filters.project,
            filters=filter_payload,
            answer_context=AnswerContext(
                summary='Search returned matching chunks based on query and metadata.',
                recommended_action=(
                    'Review source_path, heading_path, and agent_hint from the top '
                    'results first.'
                ),
            ),
            results=[
                KnowledgeSearchResult(
                    rank=index + 1,
                    score=record.score,
                    source_path=record.source_path,
                    title=record.title,
                    heading_path=record.heading_path or [],
                    type=record.document_type,
                    project=record.project,
                    domain=record.domain,
                    priority=record.priority,
                    status=record.status,
                    visibility=record.visibility,
                    tags=record.tags or [],
                    content=record.content,
                    agent_hint=record.agent_hint,
                    revision=record.revision,
                    updated_at=record.updated_at,
                )
                for index, record in enumerate(records)
            ],
        )


class SearchLogService:
    def __init__(self, *, repository: SearchRepository) -> None:
        self.repository = repository

    async def list_logs(
        self,
        *,
        vault_id: str | None,
        from_time: datetime | None,
        to_time: datetime | None,
        query: str | None,
        limit: int,
    ) -> SearchLogsResponse:
        if limit < 1 or limit > 100:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                'limit must be between 1 and 100.',
                status_code=422,
            )
        logs = await self.repository.list_logs(
            vault_id=vault_id,
            from_time=from_time,
            to_time=to_time,
            query=query,
            limit=limit,
        )
        return SearchLogsResponse(
            logs=[SearchLogItem.model_validate(log) for log in logs]
        )


def _normalize_or_raise(
    *,
    vault_id: str,
    query: str,
    filters: dict[str, Any] | None,
    top_k: int | None,
    project: str | None,
    domain: str | None,
) -> NormalizedSearchQuery:
    try:
        return normalize_search_query(
            vault_id=vault_id,
            query=query,
            filters=filters,
            top_k=top_k,
            project=project,
            domain=domain,
        )
    except DomainValidationError as exc:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            exc.message,
            status_code=422,
            details=exc.details,
        ) from exc


def _filters_payload(normalized: NormalizedSearchQuery) -> dict[str, Any]:
    filters = normalized.filters
    return {
        'types': [value.value for value in filters.types],
        'status': [value.value for value in filters.status],
        'priority': [value.value for value in filters.priority],
        'visibility': [value.value for value in filters.visibility],
        'tags': list(filters.tags),
        'project': filters.project,
        'domain': filters.domain,
        'vectorize': filters.vectorize,
    }
