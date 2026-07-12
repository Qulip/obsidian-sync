from collections.abc import Iterable
from datetime import datetime
from time import perf_counter
from typing import Any
from uuid import uuid4

from obsidian_sync.clients.ollama import OllamaClient
from obsidian_sync.core.config import Settings
from obsidian_sync.core.exceptions import AppError, ErrorCode
from obsidian_sync.domain.errors import DomainValidationError
from obsidian_sync.domain.search import (
    NormalizedSearchQuery,
    normalize_search_query,
    reciprocal_rank_fusion,
)
from obsidian_sync.repositories.search import (
    SearchLogWrite,
    SearchRepository,
    SearchResultRecord,
)
from obsidian_sync.schemas.search import (
    AnswerContext,
    KnowledgeSearchResponse,
    KnowledgeSearchResult,
    SearchFeedbackResponse,
    SearchLogItem,
    SearchLogsResponse,
)
from obsidian_sync.services.rerank import rerank_records


class KnowledgeSearchService:
    def __init__(
        self,
        *,
        repository: SearchRepository,
        ollama_client: OllamaClient,
        settings: Settings,
    ) -> None:
        self.repository = repository
        self.ollama_client = ollama_client
        self.settings = settings

    async def search(
        self,
        *,
        vault_id: str,
        query: str,
        filters: dict[str, Any] | None,
        top_k: int | None,
        project: str | None,
        domain: str | None,
        min_score: float | None,
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
            min_score=min_score,
        )
        started = perf_counter()
        request_id = uuid4().hex
        vault_pk = await self.repository.get_vault_pk(normalized.vault_id)
        if vault_pk is None:
            raise AppError(ErrorCode.NOT_FOUND, 'Vault was not found.', status_code=404)

        query_embedding = await self.ollama_client.embed(normalized.query)
        if self.settings.search_hybrid_enabled:
            candidates, matched_by = await self._hybrid_search_chunks(
                normalized=normalized, query_embedding=query_embedding
            )
        else:
            candidates = await self.repository.search_chunks(
                vault_id=normalized.vault_id,
                query_embedding=query_embedding,
                filters=normalized.filters,
                top_k=normalized.top_k,
            )
            matched_by = {record.chunk_id: 'vector' for record in candidates}
        candidate_count = len(candidates)
        effective_min_score = (
            normalized.min_score
            if normalized.min_score is not None
            else self.settings.search_min_score
        )
        records = candidates
        if effective_min_score > 0.0:
            # The cosine threshold only makes sense for candidates found
            # purely by vector similarity. A candidate with a lexical match
            # ('lexical' or 'both') can have a low cosine score for reasons
            # unrelated to relevance (e.g. an exact function name or error
            # message match whose surrounding text is semantically distant
            # from the query embedding) -- filtering those out on cosine
            # alone would silently discard the strongest keyword matches.
            records = [
                record
                for record in records
                if matched_by[record.chunk_id] != 'vector'
                or record.score >= effective_min_score
            ]
        no_candidates = candidate_count == 0
        low_confidence = candidate_count > 0 and len(records) == 0
        reranked = False
        if self.settings.search_rerank_enabled and self.settings.search_rerank_model:
            records, reranked = await self._maybe_rerank(
                query=normalized.query, records=records
            )
        records = records[: normalized.top_k]
        reported_min_score = effective_min_score if effective_min_score > 0.0 else None

        pending_vectorizing_jobs = await self.repository.count_pending_reindex(
            normalized.vault_id
        )
        failed_vectorizing_jobs = await self.repository.count_failed_reindex(
            normalized.vault_id
        )
        latency_ms = int((perf_counter() - started) * 1000)
        filter_payload = _filters_payload(normalized, effective_min_score)
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
            request_id=request_id,
            query=normalized.query,
            vault_id=normalized.vault_id,
            project=normalized.filters.project,
            filters=filter_payload,
            answer_context=_build_answer_context(
                pending_vectorizing_jobs,
                failed_vectorizing_jobs,
                low_confidence=low_confidence,
                no_candidates=no_candidates,
                has_results=bool(records),
            ),
            pending_vectorizing_jobs=pending_vectorizing_jobs,
            failed_vectorizing_jobs=failed_vectorizing_jobs,
            index_fresh=pending_vectorizing_jobs == 0 and failed_vectorizing_jobs == 0,
            min_score=reported_min_score,
            low_confidence=low_confidence,
            no_candidates=no_candidates,
            reranked=reranked,
            results=[
                KnowledgeSearchResult(
                    rank=index + 1,
                    score=record.score,
                    matched_by=matched_by[record.chunk_id],
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

    async def _hybrid_search_chunks(
        self,
        *,
        normalized: NormalizedSearchQuery,
        query_embedding: list[float],
    ) -> tuple[list[SearchResultRecord], dict[int, str]]:
        """Merge vector and lexical candidates via Reciprocal Rank Fusion.

        Each leg is queried sequentially (a single AsyncSession cannot run
        concurrent statements) for up to `search_candidate_limit` candidates,
        then merged by chunk id using RRF. The returned score on each record
        remains cosine similarity, not the RRF value -- RRF only decides
        ordering here.

        Also returns a chunk_id -> matched_by map ('vector', 'lexical', or
        'both') recording which leg(s) surfaced each candidate, so callers
        can tell apart a chunk that only ranked well semantically from one
        that was pulled in by an exact keyword match.
        """
        candidate_limit = self.settings.search_candidate_limit
        vector_records = await self.repository.search_chunks(
            vault_id=normalized.vault_id,
            query_embedding=query_embedding,
            filters=normalized.filters,
            top_k=normalized.top_k,
            candidate_limit=candidate_limit,
        )
        lexical_records = await self.repository.search_chunks_lexical(
            vault_id=normalized.vault_id,
            query_embedding=query_embedding,
            query_text=normalized.query,
            filters=normalized.filters,
            candidate_limit=candidate_limit,
        )
        records_by_id = {
            record.chunk_id: record
            for record in (*lexical_records, *vector_records)
        }
        matched_by = _build_matched_by(
            vector_ids=(record.chunk_id for record in vector_records),
            lexical_ids=(record.chunk_id for record in lexical_records),
        )
        merged_ids = reciprocal_rank_fusion(
            (
                [record.chunk_id for record in vector_records],
                [record.chunk_id for record in lexical_records],
            )
        )
        return [records_by_id[chunk_id] for chunk_id in merged_ids], matched_by

    async def _maybe_rerank(
        self, *, query: str, records: list[SearchResultRecord]
    ) -> tuple[list[SearchResultRecord], bool]:
        """Rerank the top `search_rerank_candidates` records via LLM.

        Records beyond that window are left untouched and appended after
        the reranked head. Any rerank failure (request error, malformed
        response) leaves `records` in its original order and reports
        `reranked=False` -- search must never fail because rerank did.
        """
        candidate_count = self.settings.search_rerank_candidates
        head = records[:candidate_count]
        tail = records[candidate_count:]
        reranked_head = await rerank_records(
            ollama_client=self.ollama_client,
            model=self.settings.search_rerank_model,
            query=query,
            records=head,
        )
        if reranked_head is None:
            return records, False
        return [*reranked_head, *tail], True


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
    ) -> SearchFeedbackResponse:
        if (
            helpful is None
            and selected_source_path is None
            and selected_chunk_rank is None
            and expected_missing is None
            and comment is None
        ):
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                'At least one feedback field must be provided.',
                status_code=422,
            )
        log = await self.repository.record_feedback(
            request_id=request_id,
            vault_id=vault_id,
            helpful=helpful,
            selected_source_path=selected_source_path,
            selected_chunk_rank=selected_chunk_rank,
            expected_missing=expected_missing,
            comment=comment,
        )
        if log is None:
            raise AppError(
                ErrorCode.NOT_FOUND,
                'Search log was not found for the given request_id and vault_id.',
                status_code=404,
            )
        assert log.feedback_at is not None
        return SearchFeedbackResponse(
            request_id=log.request_id,
            vault_id=log.vault_id,
            feedback_at=log.feedback_at,
        )


def _normalize_or_raise(
    *,
    vault_id: str,
    query: str,
    filters: dict[str, Any] | None,
    top_k: int | None,
    project: str | None,
    domain: str | None,
    min_score: float | None,
) -> NormalizedSearchQuery:
    try:
        return normalize_search_query(
            vault_id=vault_id,
            query=query,
            filters=filters,
            top_k=top_k,
            project=project,
            domain=domain,
            min_score=min_score,
        )
    except DomainValidationError as exc:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            exc.message,
            status_code=422,
            details=exc.details,
        ) from exc


def _build_answer_context(
    pending_vectorizing_jobs: int,
    failed_vectorizing_jobs: int,
    *,
    low_confidence: bool,
    no_candidates: bool,
    has_results: bool,
) -> AnswerContext:
    if not has_results:
        if no_candidates:
            return AnswerContext(
                summary=(
                    'No supporting evidence was found for this query -- no '
                    'vector or lexical candidate matched it.'
                ),
                recommended_action=(
                    'Do not cite these results -- there is no matching chunk '
                    'for this query. Try rephrasing the query or adjusting '
                    'filters, or check pending_vectorizing_jobs / '
                    'failed_vectorizing_jobs in case relevant notes have not '
                    'been indexed yet.'
                ),
            )
        return AnswerContext(
            summary=(
                'No supporting evidence was found for this query -- '
                'candidates existed but none met the minimum relevance '
                'threshold.'
            ),
            recommended_action=(
                'Do not cite these results -- no chunk was relevant enough to '
                'the query. Consider rephrasing the query, lowering min_score, '
                'or reporting that no matching notes were found.'
            ),
        )
    if pending_vectorizing_jobs == 0 and failed_vectorizing_jobs == 0:
        return AnswerContext(
            summary='Search returned matching chunks based on query and metadata.',
            recommended_action=(
                'Review source_path, heading_path, and agent_hint from the top '
                'results first.'
            ),
        )
    notices: list[str] = []
    if pending_vectorizing_jobs > 0:
        notices.append(
            f'{pending_vectorizing_jobs} file(s) are not yet indexed, so '
            'results may be incomplete.'
        )
    if failed_vectorizing_jobs > 0:
        notices.append(
            f'{failed_vectorizing_jobs} file(s) failed indexing and are '
            'missing from search results; check index failure logs.'
        )
    return AnswerContext(
        summary=(
            'Search returned matching chunks based on query and metadata. '
            + ' '.join(notices)
        ),
        recommended_action=(
            'Review source_path, heading_path, and agent_hint from the top '
            'results first. Some files are still pending or failed '
            'vectorization -- run reindex_vault(mode=changed_only) and '
            'search again for complete results.'
        ),
    )


def _build_matched_by(
    *, vector_ids: Iterable[int], lexical_ids: Iterable[int]
) -> dict[int, str]:
    vector_id_set = set(vector_ids)
    lexical_id_set = set(lexical_ids)
    matched_by: dict[int, str] = {}
    for chunk_id in vector_id_set | lexical_id_set:
        in_vector = chunk_id in vector_id_set
        in_lexical = chunk_id in lexical_id_set
        if in_vector and in_lexical:
            matched_by[chunk_id] = 'both'
        elif in_lexical:
            matched_by[chunk_id] = 'lexical'
        else:
            matched_by[chunk_id] = 'vector'
    return matched_by


def _filters_payload(
    normalized: NormalizedSearchQuery, effective_min_score: float
) -> dict[str, Any]:
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
        'min_score': effective_min_score,
    }
