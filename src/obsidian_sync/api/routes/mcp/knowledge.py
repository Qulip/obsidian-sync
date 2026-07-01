from fastapi import APIRouter

from obsidian_sync.api.deps import (
    DbSessionDependency,
    RequestMetadataDependency,
    SettingsDependency,
)
from obsidian_sync.clients.ollama import OllamaClient
from obsidian_sync.core.responses import ResponseEnvelope, ok
from obsidian_sync.repositories.search import SearchRepository
from obsidian_sync.schemas.search import KnowledgeSearchRequest, KnowledgeSearchResponse
from obsidian_sync.services.search import KnowledgeSearchService

router = APIRouter(prefix='/mcp/knowledge', tags=['mcp'])


@router.post('/search', response_model=ResponseEnvelope[KnowledgeSearchResponse])
async def search_knowledge(
    payload: KnowledgeSearchRequest,
    session: DbSessionDependency,
    settings: SettingsDependency,
    metadata: RequestMetadataDependency,
) -> ResponseEnvelope[KnowledgeSearchResponse]:
    """Search personal learning notes using semantic vector search.

    Call this before answering questions about coding patterns, technical topics,
    troubleshooting records, or work-related subjects — the user may have relevant
    notes already saved. Returns the most semantically similar note chunks with
    source path, tags, and agent_hint for context.

    Workflow: list_vaults → search_knowledge → use results to inform your answer.
    """
    service = KnowledgeSearchService(
        repository=SearchRepository(session),
        ollama_client=OllamaClient(
            base_url=settings.ollama_base_url,
            model=settings.embedding_model,
            timeout_seconds=settings.ollama_timeout_seconds,
        ),
    )
    return ok(
        await service.search(
            vault_id=payload.vault_id,
            query=payload.query,
            filters=payload.filters,
            top_k=payload.top_k,
            project=payload.project,
            domain=payload.domain,
            token_id=metadata.token_id,
            client_ip=metadata.client_ip,
            user_agent=metadata.user_agent,
        )
    )
