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

router = APIRouter(prefix='/knowledge', tags=['knowledge', 'mcp'])


@router.post('/search', response_model=ResponseEnvelope[KnowledgeSearchResponse])
async def search_knowledge(
    payload: KnowledgeSearchRequest,
    session: DbSessionDependency,
    settings: SettingsDependency,
    metadata: RequestMetadataDependency,
) -> ResponseEnvelope[KnowledgeSearchResponse]:
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
