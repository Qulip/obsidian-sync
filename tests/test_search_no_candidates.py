"""Service-level tests distinguishing "no candidates at all" from
"candidates existed but were filtered by min_score".

These exercise KnowledgeSearchService.search directly against a real
PostgreSQL database (see tests/conftest.py), with the Ollama embedding
call stubbed out via AsyncMock so no live Ollama server is required.
"""

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from obsidian_sync.clients.ollama import OllamaClient
from obsidian_sync.core.config import get_settings
from obsidian_sync.db.models import Vault
from obsidian_sync.db.session import build_async_engine, build_sessionmaker
from obsidian_sync.repositories.search import SearchRepository
from obsidian_sync.services.search import KnowledgeSearchService

VAULT_ID = 'no-candidates-vault'
EMBEDDING_DIMENSIONS = 1024


def _query_embedding() -> list[float]:
    return [1.0] + [0.0] * (EMBEDDING_DIMENSIONS - 1)


@pytest.fixture
async def db_session(clean_db: None) -> AsyncIterator[AsyncSession]:
    settings = get_settings()
    assert settings.database_url is not None
    engine = build_async_engine(settings.database_url)
    sessionmaker = build_sessionmaker(engine)
    async with sessionmaker() as session:
        yield session
    await engine.dispose()


def _stub_ollama_client() -> OllamaClient:
    client = AsyncMock(spec=OllamaClient)
    client.embed.return_value = _query_embedding()
    return client


async def _seed_empty_vault(session: AsyncSession) -> None:
    session.add(Vault(vault_id=VAULT_ID, name='No Candidates Vault'))
    await session.flush()


async def _search(db_session: AsyncSession) -> KnowledgeSearchService:
    return KnowledgeSearchService(
        repository=SearchRepository(db_session),
        ollama_client=_stub_ollama_client(),
        settings=get_settings(),
    )


async def test_search_reports_no_candidates_when_vault_has_no_chunks(
    db_session: AsyncSession,
) -> None:
    await _seed_empty_vault(db_session)

    service = await _search(db_session)
    response = await service.search(
        vault_id=VAULT_ID,
        query='anything at all',
        filters=None,
        top_k=None,
        project=None,
        domain=None,
        min_score=None,
        token_id=None,
        client_ip=None,
        user_agent=None,
    )

    assert response.results == []
    assert response.no_candidates is True
    assert response.low_confidence is False
    assert 'no supporting evidence' in response.answer_context.summary.lower()
