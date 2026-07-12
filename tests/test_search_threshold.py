"""Service-level tests for search min_score threshold and no-result handling.

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
from obsidian_sync.core.exceptions import AppError, ErrorCode
from obsidian_sync.db.models import KnowledgeChunk, Vault, VaultFile
from obsidian_sync.db.session import build_async_engine, build_sessionmaker
from obsidian_sync.repositories.search import SearchRepository
from obsidian_sync.services.search import KnowledgeSearchService

VAULT_ID = 'threshold-vault'
SOURCE_PATH = 'notes/threshold.md'
EMBEDDING_DIMENSIONS = 1024


def _query_embedding() -> list[float]:
    return [1.0] + [0.0] * (EMBEDDING_DIMENSIONS - 1)


def _orthogonal_embedding() -> list[float]:
    return [0.0, 1.0] + [0.0] * (EMBEDDING_DIMENSIONS - 2)


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


async def _seed_vault(session: AsyncSession) -> int:
    vault = Vault(vault_id=VAULT_ID, name='Threshold Vault')
    session.add(vault)
    await session.flush()
    return vault.id


async def _seed_indexed_file(
    session: AsyncSession, *, vault_pk: int, embedding: list[float]
) -> None:
    session.add(
        VaultFile(
            vault_pk=vault_pk,
            vault_id=VAULT_ID,
            source_path=SOURCE_PATH,
            content_hash='hash-threshold',
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
            source_path=SOURCE_PATH,
            chunk_index=0,
            title='Threshold',
            content='Threshold content body.',
            content_hash='hash-threshold',
            embedding=embedding,
        )
    )
    await session.flush()


async def _search(db_session: AsyncSession) -> KnowledgeSearchService:
    return KnowledgeSearchService(
        repository=SearchRepository(db_session),
        ollama_client=_stub_ollama_client(),
        settings=get_settings(),
    )


async def test_search_returns_all_results_when_threshold_disabled(
    db_session: AsyncSession,
) -> None:
    vault_pk = await _seed_vault(db_session)
    await _seed_indexed_file(
        db_session, vault_pk=vault_pk, embedding=_query_embedding()
    )

    service = await _search(db_session)
    response = await service.search(
        vault_id=VAULT_ID,
        query='threshold content',
        filters=None,
        top_k=None,
        project=None,
        domain=None,
        min_score=None,
        token_id=None,
        client_ip=None,
        user_agent=None,
    )

    assert len(response.results) == 1
    assert response.low_confidence is False
    assert response.min_score is None


async def test_search_reports_low_confidence_when_min_score_filters_all(
    db_session: AsyncSession,
) -> None:
    vault_pk = await _seed_vault(db_session)
    await _seed_indexed_file(
        db_session, vault_pk=vault_pk, embedding=_orthogonal_embedding()
    )

    service = await _search(db_session)
    response = await service.search(
        vault_id=VAULT_ID,
        query='threshold content',
        filters=None,
        top_k=None,
        project=None,
        domain=None,
        min_score=0.99,
        token_id=None,
        client_ip=None,
        user_agent=None,
    )

    assert response.results == []
    assert response.low_confidence is True
    assert response.min_score == 0.99
    assert 'no supporting evidence' in response.answer_context.summary.lower()


async def test_search_rejects_min_score_out_of_range(
    db_session: AsyncSession,
) -> None:
    vault_pk = await _seed_vault(db_session)
    await _seed_indexed_file(
        db_session, vault_pk=vault_pk, embedding=_query_embedding()
    )

    service = await _search(db_session)
    with pytest.raises(AppError) as excinfo:
        await service.search(
            vault_id=VAULT_ID,
            query='threshold content',
            filters=None,
            top_k=None,
            project=None,
            domain=None,
            min_score=1.5,
            token_id=None,
            client_ip=None,
            user_agent=None,
        )

    assert excinfo.value.code == ErrorCode.VALIDATION_ERROR
    assert excinfo.value.status_code == 422
