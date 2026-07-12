"""Tests for matched_by tagging and the lexical-match min_score exemption.

Hybrid search merges a vector leg and a lexical (PostgreSQL full-text) leg
via Reciprocal Rank Fusion, but the response `score` field is always cosine
similarity -- a chunk that is a strong lexical match (an exact function
name, an error message) can have a low cosine score for reasons unrelated
to relevance. `KnowledgeSearchService.search` therefore only applies the
`min_score` cosine threshold to candidates whose only match is the vector
leg; candidates also found by the lexical leg ('lexical' or 'both') are
exempt. These tests pin that policy down and confirm each result reports
which leg(s) found it via `matched_by`.

Follows the fixture pattern used by tests/test_search_hybrid.py and
tests/test_search_ranking.py: deterministic 2D basis embeddings on the
(e0, e1) plane so cosine similarity to the query vector (always e0) can be
reasoned about by hand, combined with a stubbed OllamaClient.
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

VAULT_ID = 'matched-by-vault'
EMBEDDING_DIMENSIONS = 1024


def _basis_vector(a: float, b: float) -> list[float]:
    vector = [0.0] * EMBEDDING_DIMENSIONS
    vector[0] = a
    vector[1] = b
    return vector


def _query_vector() -> list[float]:
    return _basis_vector(1.0, 0.0)


def _orthogonal_vector() -> list[float]:
    return _basis_vector(0.0, 1.0)


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
    client.embed.return_value = _query_vector()
    return client


def _search_service(
    db_session: AsyncSession,
    *,
    hybrid_enabled: bool,
    candidate_limit: int | None = None,
) -> KnowledgeSearchService:
    update: dict[str, object] = {'search_hybrid_enabled': hybrid_enabled}
    if candidate_limit is not None:
        update['search_candidate_limit'] = candidate_limit
    settings = get_settings().model_copy(update=update)
    return KnowledgeSearchService(
        repository=SearchRepository(db_session),
        ollama_client=_stub_ollama_client(),
        settings=settings,
    )


async def _seed_vault(session: AsyncSession, name: str = 'Matched-By Vault') -> int:
    vault = Vault(vault_id=VAULT_ID, name=name)
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


async def test_lexical_only_match_is_exempt_from_min_score(
    db_session: AsyncSession,
) -> None:
    """A chunk found only by the lexical leg (pushed out of the vector
    leg's small candidate window by two perfect-cosine decoys) must survive
    a min_score threshold its near-zero cosine score would otherwise fail,
    and must report matched_by='lexical'.
    """
    vault_pk = await _seed_vault(db_session)

    for index in range(2):
        await _seed_file_and_chunk(
            db_session,
            vault_pk=vault_pk,
            source_path=f'notes/decoy-{index}.md',
            embedding=_query_vector(),
            content=f'Generic decoy content number {index} about nothing special.',
        )

    await _seed_file_and_chunk(
        db_session,
        vault_pk=vault_pk,
        source_path='notes/lexical-only-target.md',
        embedding=_orthogonal_vector(),
        content='This note documents FunctionXyzUnique and its behavior.',
    )

    # candidate_limit=2 keeps the vector leg's window to exactly the two
    # perfect-cosine decoys, so the orthogonal target is only reachable
    # through the lexical leg.
    service = _search_service(db_session, hybrid_enabled=True, candidate_limit=2)
    response = await service.search(
        vault_id=VAULT_ID,
        query='FunctionXyzUnique',
        filters=None,
        top_k=5,
        project=None,
        domain=None,
        min_score=0.9,
        token_id=None,
        client_ip=None,
        user_agent=None,
    )

    results_by_path = {result.source_path: result for result in response.results}
    assert 'notes/lexical-only-target.md' in results_by_path
    target = results_by_path['notes/lexical-only-target.md']
    assert target.matched_by == 'lexical'
    assert target.score == pytest.approx(0.0, abs=1e-6)


async def test_vector_only_candidate_is_still_filtered_by_min_score(
    db_session: AsyncSession,
) -> None:
    """A candidate with no lexical match at all must still be removed by
    min_score -- the lexical exemption must not become a blanket bypass.
    """
    vault_pk = await _seed_vault(db_session)
    await _seed_file_and_chunk(
        db_session,
        vault_pk=vault_pk,
        source_path='notes/vector-only.md',
        embedding=_orthogonal_vector(),
        content='Generic content sharing no terms with the search query.',
    )

    service = _search_service(db_session, hybrid_enabled=True)
    response = await service.search(
        vault_id=VAULT_ID,
        query='wholly unrelated inquiry phrase',
        filters=None,
        top_k=5,
        project=None,
        domain=None,
        min_score=0.9,
        token_id=None,
        client_ip=None,
        user_agent=None,
    )

    assert response.results == []
    assert response.low_confidence is True
    assert response.no_candidates is False


async def test_chunk_matched_by_both_legs_is_tagged_both(
    db_session: AsyncSession,
) -> None:
    vault_pk = await _seed_vault(db_session)
    await _seed_file_and_chunk(
        db_session,
        vault_pk=vault_pk,
        source_path='notes/both-legs.md',
        embedding=_query_vector(),
        content='This chunk mentions UniqueTermBoth in its body.',
    )

    service = _search_service(db_session, hybrid_enabled=True)
    response = await service.search(
        vault_id=VAULT_ID,
        query='UniqueTermBoth',
        filters=None,
        top_k=5,
        project=None,
        domain=None,
        min_score=None,
        token_id=None,
        client_ip=None,
        user_agent=None,
    )

    assert len(response.results) == 1
    assert response.results[0].matched_by == 'both'


async def test_hybrid_disabled_reports_matched_by_vector(
    db_session: AsyncSession,
) -> None:
    vault_pk = await _seed_vault(db_session)
    await _seed_file_and_chunk(
        db_session,
        vault_pk=vault_pk,
        source_path='notes/vector-mode.md',
        embedding=_query_vector(),
        content='Content irrelevant to matched_by tagging under vector-only mode.',
    )

    service = _search_service(db_session, hybrid_enabled=False)
    response = await service.search(
        vault_id=VAULT_ID,
        query='anything',
        filters=None,
        top_k=5,
        project=None,
        domain=None,
        min_score=None,
        token_id=None,
        client_ip=None,
        user_agent=None,
    )

    assert len(response.results) == 1
    assert response.results[0].matched_by == 'vector'
