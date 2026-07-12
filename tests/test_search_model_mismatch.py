"""Service/repository tests for embedding-model mismatch handling in search.

When the configured embedding model changes, chunks embedded with the
previous model must not be compared against a query embedding produced by
the new model in the same vector space. These tests exercise
KnowledgeSearchService.search directly against a real PostgreSQL database
(see tests/conftest.py), with the Ollama embedding call stubbed out via
AsyncMock so no live Ollama server is required.
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

VAULT_ID = 'model-mismatch-vault'
CURRENT_PATH = 'notes/current-model.md'
STALE_PATH = 'notes/stale-model.md'
STALE_MODEL = 'old-model'
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
    vault = Vault(vault_id=VAULT_ID, name='Model Mismatch Vault')
    session.add(vault)
    await session.flush()
    return vault.id


async def _seed_file_with_chunk(
    session: AsyncSession,
    *,
    vault_pk: int,
    source_path: str,
    embedding_model: str,
) -> None:
    content_hash = f'hash-{source_path}'
    session.add(
        VaultFile(
            vault_pk=vault_pk,
            vault_id=VAULT_ID,
            source_path=source_path,
            content_hash=content_hash,
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
            source_path=source_path,
            chunk_index=0,
            title=source_path,
            content=f'Content body for {source_path}.',
            content_hash=content_hash,
            embedding=_embedding(),
            embedding_model=embedding_model,
        )
    )
    await session.flush()


async def _search(db_session: AsyncSession) -> KnowledgeSearchService:
    return KnowledgeSearchService(
        repository=SearchRepository(db_session),
        ollama_client=_stub_ollama_client(),
        settings=get_settings(),
    )


async def test_current_model_chunk_is_returned_and_reports_fresh(
    db_session: AsyncSession,
) -> None:
    current_model = get_settings().embedding_model
    vault_pk = await _seed_vault(db_session)
    await _seed_file_with_chunk(
        db_session,
        vault_pk=vault_pk,
        source_path=CURRENT_PATH,
        embedding_model=current_model,
    )

    service = await _search(db_session)
    response = await service.search(
        vault_id=VAULT_ID,
        query='content body',
        filters=None,
        top_k=None,
        project=None,
        domain=None,
        min_score=None,
        token_id=None,
        client_ip=None,
        user_agent=None,
    )

    assert [result.source_path for result in response.results] == [CURRENT_PATH]
    assert response.model_stale_jobs == 0
    assert response.index_fresh is True


async def test_stale_model_chunk_is_excluded_and_reports_model_stale(
    db_session: AsyncSession,
) -> None:
    vault_pk = await _seed_vault(db_session)
    await _seed_file_with_chunk(
        db_session,
        vault_pk=vault_pk,
        source_path=STALE_PATH,
        embedding_model=STALE_MODEL,
    )

    service = await _search(db_session)
    response = await service.search(
        vault_id=VAULT_ID,
        query='content body',
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
    assert response.model_stale_jobs == 1
    assert response.index_fresh is False
    summary = response.answer_context.summary.lower()
    assert 'different embedding model' in summary
    assert 'full' in summary


async def test_mixed_models_only_returns_current_model_chunk(
    db_session: AsyncSession,
) -> None:
    current_model = get_settings().embedding_model
    vault_pk = await _seed_vault(db_session)
    await _seed_file_with_chunk(
        db_session,
        vault_pk=vault_pk,
        source_path=CURRENT_PATH,
        embedding_model=current_model,
    )
    await _seed_file_with_chunk(
        db_session,
        vault_pk=vault_pk,
        source_path=STALE_PATH,
        embedding_model=STALE_MODEL,
    )

    service = await _search(db_session)
    response = await service.search(
        vault_id=VAULT_ID,
        query='content body',
        filters=None,
        top_k=None,
        project=None,
        domain=None,
        min_score=None,
        token_id=None,
        client_ip=None,
        user_agent=None,
    )

    assert [result.source_path for result in response.results] == [CURRENT_PATH]
    assert response.model_stale_jobs == 1
    assert response.index_fresh is False
