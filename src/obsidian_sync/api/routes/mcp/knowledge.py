from fastapi import APIRouter

from obsidian_sync.api.deps import (
    DbSessionDependency,
    RequestMetadataDependency,
    SettingsDependency,
)
from obsidian_sync.clients.ollama import OllamaClient
from obsidian_sync.core.responses import ResponseEnvelope, ok
from obsidian_sync.repositories.search import SearchRepository
from obsidian_sync.schemas.mcp import McpKnowledgeSearchRequest
from obsidian_sync.schemas.search import (
    KnowledgeSearchResponse,
    SearchFeedbackRequest,
    SearchFeedbackResponse,
)
from obsidian_sync.services.search import KnowledgeSearchService, SearchLogService

router = APIRouter(prefix='/mcp/knowledge', tags=['mcp'])


@router.post('/search', response_model=ResponseEnvelope[KnowledgeSearchResponse])
async def search_knowledge(
    payload: McpKnowledgeSearchRequest,
    session: DbSessionDependency,
    settings: SettingsDependency,
    metadata: RequestMetadataDependency,
) -> ResponseEnvelope[KnowledgeSearchResponse]:
    """Search personal learning notes using semantic vector search.

    Call this before answering questions about coding patterns, technical topics,
    troubleshooting records, or work-related subjects — the user may have relevant
    notes already saved. Returns the most semantically similar note chunks with
    source path, tags, and agent_hint for context.

    filters fields — types: reference|rule|decision|issue-solution|study-note|prompt|
    command|checklist. status: current|draft|deprecated|archived.
    priority: high|medium|low. visibility: personal|company|confidential|public.
    tags: list of strings.

    Workflow: list_vaults → search_knowledge → use results to inform your answer.
    """
    filters = payload.filters.model_dump(exclude_none=True) if payload.filters else None
    service = KnowledgeSearchService(
        repository=SearchRepository(session),
        ollama_client=OllamaClient(
            base_url=settings.ollama_base_url,
            model=settings.embedding_model,
            timeout_seconds=settings.ollama_timeout_seconds,
        ),
        settings=settings,
    )
    return ok(
        await service.search(
            vault_id=payload.vault_id,
            query=payload.query,
            filters=filters,
            top_k=payload.top_k,
            project=payload.project,
            domain=payload.domain,
            min_score=payload.min_score,
            token_id=metadata.token_id,
            client_ip=metadata.client_ip,
            user_agent=metadata.user_agent,
        )
    )


@router.post(
    '/search/feedback', response_model=ResponseEnvelope[SearchFeedbackResponse]
)
async def submit_search_feedback(
    payload: SearchFeedbackRequest,
    session: DbSessionDependency,
) -> ResponseEnvelope[SearchFeedbackResponse]:
    """Record feedback for a previous search_knowledge call.

    Attach `request_id` from a prior search response and note whether the
    results were helpful, which result was actually used, or whether the
    expected note was missing. Used to tune search thresholds and ranking.
    """
    service = SearchLogService(repository=SearchRepository(session))
    return ok(
        await service.record_feedback(
            request_id=payload.request_id,
            vault_id=payload.vault_id,
            helpful=payload.helpful,
            selected_source_path=payload.selected_source_path,
            selected_chunk_rank=payload.selected_chunk_rank,
            expected_missing=payload.expected_missing,
            comment=payload.comment,
        )
    )
