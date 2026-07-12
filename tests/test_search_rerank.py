"""Service-level tests for the optional LLM listwise rerank step.

These exercise KnowledgeSearchService.search directly against a real
PostgreSQL database (see tests/conftest.py), following the fixture pattern
used by tests/test_search_hybrid.py: deterministic 2D basis embeddings on
the (e0, e1) plane so cosine similarity to the query vector (always e0) can
be reasoned about by hand. Both `OllamaClient.embed` and
`OllamaClient.generate` are stubbed via AsyncMock so no live Ollama server
is required.
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

VAULT_ID = 'rerank-vault'
EMBEDDING_DIMENSIONS = 1024


def _basis_vector(a: float, b: float) -> list[float]:
    vector = [0.0] * EMBEDDING_DIMENSIONS
    vector[0] = a
    vector[1] = b
    return vector


def _query_vector() -> list[float]:
    return _basis_vector(1.0, 0.0)


@pytest.fixture
async def db_session(clean_db: None) -> AsyncIterator[AsyncSession]:
    settings = get_settings()
    assert settings.database_url is not None
    engine = build_async_engine(settings.database_url)
    sessionmaker = build_sessionmaker(engine)
    async with sessionmaker() as session:
        yield session
    await engine.dispose()


def _stub_ollama_client(*, generate_side_effect: object = None) -> OllamaClient:
    client = AsyncMock(spec=OllamaClient)
    client.embed.return_value = _query_vector()
    if generate_side_effect is not None:
        client.generate.side_effect = generate_side_effect
    return client


def _search_service(
    db_session: AsyncSession,
    *,
    rerank_enabled: bool,
    rerank_model: str = 'qwen3:8b',
    generate_side_effect: object = None,
) -> KnowledgeSearchService:
    settings = get_settings().model_copy(
        update={
            'search_hybrid_enabled': False,
            'search_rerank_enabled': rerank_enabled,
            'search_rerank_model': rerank_model,
            'search_rerank_candidates': 15,
        }
    )
    return KnowledgeSearchService(
        repository=SearchRepository(db_session),
        ollama_client=_stub_ollama_client(generate_side_effect=generate_side_effect),
        settings=settings,
    )


async def _seed_vault(session: AsyncSession) -> int:
    vault = Vault(vault_id=VAULT_ID, name='Rerank Vault')
    session.add(vault)
    await session.flush()
    return vault.id


async def _seed_file_and_chunk(
    session: AsyncSession,
    *,
    vault_pk: int,
    source_path: str,
    embedding: list[float],
    content: str,
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
            title=f'Chunk of {source_path}',
            content=content,
            content_hash=content_hash,
            embedding=embedding,
        )
    )
    await session.flush()


async def _seed_three_ranked_chunks(session: AsyncSession, vault_pk: int) -> None:
    # Vector-only ranking, best to worst cosine similarity to the query
    # vector (1.0, 0.0): best.md, mid.md, worst.md.
    await _seed_file_and_chunk(
        session,
        vault_pk=vault_pk,
        source_path='notes/best.md',
        embedding=_basis_vector(1.0, 0.0),
        content='Best vector match content.',
    )
    await _seed_file_and_chunk(
        session,
        vault_pk=vault_pk,
        source_path='notes/mid.md',
        embedding=_basis_vector(0.8, 0.2),
        content='Mid vector match content.',
    )
    await _seed_file_and_chunk(
        session,
        vault_pk=vault_pk,
        source_path='notes/worst.md',
        embedding=_basis_vector(0.0, 1.0),
        content='Worst vector match content.',
    )


async def test_rerank_disabled_by_default_keeps_original_ranking(
    db_session: AsyncSession,
) -> None:
    vault_pk = await _seed_vault(db_session)
    await _seed_three_ranked_chunks(db_session, vault_pk)

    service = _search_service(db_session, rerank_enabled=False)
    response = await service.search(
        vault_id=VAULT_ID,
        query='ranking query',
        filters=None,
        top_k=3,
        project=None,
        domain=None,
        min_score=None,
        token_id=None,
        client_ip=None,
        user_agent=None,
    )

    result_paths = [result.source_path for result in response.results]
    assert result_paths == ['notes/best.md', 'notes/mid.md', 'notes/worst.md']
    assert response.reranked is False
    service.ollama_client.generate.assert_not_called()  # type: ignore[attr-defined]


async def test_rerank_enabled_with_reversed_llm_order_flips_ranking(
    db_session: AsyncSession,
) -> None:
    vault_pk = await _seed_vault(db_session)
    await _seed_three_ranked_chunks(db_session, vault_pk)

    service = _search_service(
        db_session,
        rerank_enabled=True,
        generate_side_effect=['[3, 2, 1]'],
    )
    response = await service.search(
        vault_id=VAULT_ID,
        query='ranking query',
        filters=None,
        top_k=3,
        project=None,
        domain=None,
        min_score=None,
        token_id=None,
        client_ip=None,
        user_agent=None,
    )

    result_paths = [result.source_path for result in response.results]
    assert result_paths == ['notes/worst.md', 'notes/mid.md', 'notes/best.md']
    assert response.reranked is True
    # Cosine score must remain what it was -- rerank only changes order.
    scores_by_path = {result.source_path: result.score for result in response.results}
    assert scores_by_path['notes/best.md'] > scores_by_path['notes/worst.md']


async def test_rerank_falls_back_to_original_ranking_on_llm_failure(
    db_session: AsyncSession,
) -> None:
    vault_pk = await _seed_vault(db_session)
    await _seed_three_ranked_chunks(db_session, vault_pk)

    service = _search_service(
        db_session,
        rerank_enabled=True,
        generate_side_effect=RuntimeError('ollama unreachable'),
    )
    response = await service.search(
        vault_id=VAULT_ID,
        query='ranking query',
        filters=None,
        top_k=3,
        project=None,
        domain=None,
        min_score=None,
        token_id=None,
        client_ip=None,
        user_agent=None,
    )

    result_paths = [result.source_path for result in response.results]
    assert result_paths == ['notes/best.md', 'notes/mid.md', 'notes/worst.md']
    assert response.reranked is False


async def test_rerank_with_missing_model_name_stays_disabled(
    db_session: AsyncSession,
) -> None:
    """search_rerank_enabled=True but no model configured must not call Ollama."""
    vault_pk = await _seed_vault(db_session)
    await _seed_three_ranked_chunks(db_session, vault_pk)

    service = _search_service(db_session, rerank_enabled=True, rerank_model='')
    response = await service.search(
        vault_id=VAULT_ID,
        query='ranking query',
        filters=None,
        top_k=3,
        project=None,
        domain=None,
        min_score=None,
        token_id=None,
        client_ip=None,
        user_agent=None,
    )

    assert response.reranked is False
    service.ollama_client.generate.assert_not_called()  # type: ignore[attr-defined]
