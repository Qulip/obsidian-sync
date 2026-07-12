"""Service-level tests for search index freshness reporting.

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
from obsidian_sync.db.models import KnowledgeChunk, Vault, VaultFile
from obsidian_sync.db.session import build_async_engine, build_sessionmaker
from obsidian_sync.repositories.search import SearchRepository
from obsidian_sync.services.search import KnowledgeSearchService

VAULT_ID = 'freshness-vault'
INDEXED_PATH = 'notes/indexed.md'
PENDING_PATH = 'notes/pending.md'
EMBEDDING_DIMENSIONS = 1024


def _embedding(seed: float = 1.0) -> list[float]:
    return [seed] + [0.0] * (EMBEDDING_DIMENSIONS - 1)


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
    client.embed.return_value = _embedding()
    return client


async def _seed_vault(session: AsyncSession) -> int:
    vault = Vault(vault_id=VAULT_ID, name='Freshness Vault')
    session.add(vault)
    await session.flush()
    return vault.id


async def _seed_indexed_file(session: AsyncSession, *, vault_pk: int) -> None:
    session.add(
        VaultFile(
            vault_pk=vault_pk,
            vault_id=VAULT_ID,
            source_path=INDEXED_PATH,
            content_hash='hash-indexed',
            index_status='indexed',
            vectorize=True,
            deleted=False,
            revision=1,
        )
    )
    session.add(
        KnowledgeChunk(
            vault_pk=vault_pk,
            vault_id=VAULT_ID,
            source_path=INDEXED_PATH,
            chunk_index=0,
            title='Indexed',
            content='Indexed content body.',
            content_hash='hash-indexed',
            embedding=_embedding(),
        )
    )
    await session.flush()


async def _seed_pending_file(session: AsyncSession, *, vault_pk: int) -> None:
    session.add(
        VaultFile(
            vault_pk=vault_pk,
            vault_id=VAULT_ID,
            source_path=PENDING_PATH,
            content_hash='hash-pending',
            index_status='pending',
            vectorize=True,
            deleted=False,
            revision=1,
        )
    )
    await session.flush()


async def _search(db_session: AsyncSession) -> KnowledgeSearchService:
    return KnowledgeSearchService(
        repository=SearchRepository(db_session),
        ollama_client=_stub_ollama_client(),
        settings=get_settings(),
    )


async def test_search_reports_fresh_index_when_nothing_pending(
    db_session: AsyncSession,
) -> None:
    vault_pk = await _seed_vault(db_session)
    await _seed_indexed_file(db_session, vault_pk=vault_pk)

    service = await _search(db_session)
    response = await service.search(
        vault_id=VAULT_ID,
        query='indexed content',
        filters=None,
        top_k=None,
        project=None,
        domain=None,
        min_score=None,
        token_id=None,
        client_ip=None,
        user_agent=None,
    )

    assert response.pending_vectorizing_jobs == 0
    assert response.index_fresh is True


async def test_search_reports_stale_index_when_reindex_pending(
    db_session: AsyncSession,
) -> None:
    vault_pk = await _seed_vault(db_session)
    await _seed_indexed_file(db_session, vault_pk=vault_pk)
    await _seed_pending_file(db_session, vault_pk=vault_pk)

    service = await _search(db_session)
    response = await service.search(
        vault_id=VAULT_ID,
        query='indexed content',
        filters=None,
        top_k=None,
        project=None,
        domain=None,
        min_score=None,
        token_id=None,
        client_ip=None,
        user_agent=None,
    )

    assert response.pending_vectorizing_jobs == 1
    assert response.index_fresh is False
    assert 'pending' in response.answer_context.recommended_action.lower()
    assert 'not yet indexed' in response.answer_context.summary.lower()
