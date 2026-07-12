"""Integration tests for PostgreSQL full-text hybrid search.

These exercise SearchRepository.search_chunks_lexical and
KnowledgeSearchService's RRF merge against a real PostgreSQL database (see
tests/conftest.py), following the fixture pattern used by
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

VAULT_ID = 'hybrid-vault'
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


def _stub_ollama_client() -> OllamaClient:
    client = AsyncMock(spec=OllamaClient)
    client.embed.return_value = _query_vector()
    return client


def _search_service(
    db_session: AsyncSession, *, hybrid_enabled: bool
) -> KnowledgeSearchService:
    settings = get_settings().model_copy(
        update={'search_hybrid_enabled': hybrid_enabled}
    )
    return KnowledgeSearchService(
        repository=SearchRepository(db_session),
        ollama_client=_stub_ollama_client(),
        settings=settings,
    )


async def _seed_vault(session: AsyncSession, name: str = 'Hybrid Vault') -> int:
    vault = Vault(vault_id=VAULT_ID, name=name)
    session.add(vault)
    await session.flush()
    return vault.id


async def _seed_file(
    session: AsyncSession, *, vault_pk: int, source_path: str, content_hash: str
) -> None:
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
    await session.flush()


async def _seed_chunk(
    session: AsyncSession,
    *,
    vault_pk: int,
    source_path: str,
    content_hash: str,
    embedding: list[float],
    content: str,
    title: str | None = None,
) -> None:
    session.add(
        KnowledgeChunk(
            vault_pk=vault_pk,
            vault_id=VAULT_ID,
            source_path=source_path,
            chunk_index=0,
            title=title or f'Chunk of {source_path}',
            content=content,
            content_hash=content_hash,
            embedding=embedding,
        )
    )
    await session.flush()


async def _seed_file_and_chunk(
    session: AsyncSession,
    *,
    vault_pk: int,
    source_path: str,
    embedding: list[float],
    content: str,
) -> None:
    content_hash = f'hash-{source_path}'
    await _seed_file(
        session, vault_pk=vault_pk, source_path=source_path, content_hash=content_hash
    )
    await _seed_chunk(
        session,
        vault_pk=vault_pk,
        source_path=source_path,
        content_hash=content_hash,
        embedding=embedding,
        content=content,
    )


async def test_hybrid_search_finds_lexical_match_missed_by_vector_only(
    db_session: AsyncSession,
) -> None:
    """A chunk near-orthogonal to the query embedding but containing the
    exact search term should surface via the lexical leg even though its
    cosine score alone would place it outside a small vector-only top_k.
    """
    vault_pk = await _seed_vault(db_session)

    # Three decoys with a perfect cosine match to the query vector -- these
    # fill every slot of a vector-only top_k=3 search.
    for index in range(3):
        await _seed_file_and_chunk(
            db_session,
            vault_pk=vault_pk,
            source_path=f'notes/decoy-{index}.md',
            embedding=_basis_vector(1.0, 0.0),
            content=f'Generic decoy content number {index} about nothing special.',
        )

    # Near-orthogonal embedding (cosine score ~= 0) but unique lexical term.
    await _seed_file_and_chunk(
        db_session,
        vault_pk=vault_pk,
        source_path='notes/target-unique.md',
        embedding=_basis_vector(0.0, 1.0),
        content='This note documents FunctionXyzUnique and its behavior.',
    )

    hybrid_service = _search_service(db_session, hybrid_enabled=True)
    response = await hybrid_service.search(
        vault_id=VAULT_ID,
        query='FunctionXyzUnique',
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
    assert 'notes/target-unique.md' in result_paths


async def test_hybrid_disabled_falls_back_to_vector_only_search(
    db_session: AsyncSession,
) -> None:
    """With search_hybrid_enabled=False, the lexical leg must not run --
    the same near-orthogonal-but-lexically-unique chunk from the previous
    test should be excluded once vector similarity alone determines top_k.
    """
    vault_pk = await _seed_vault(db_session)

    for index in range(3):
        await _seed_file_and_chunk(
            db_session,
            vault_pk=vault_pk,
            source_path=f'notes/decoy-{index}.md',
            embedding=_basis_vector(1.0, 0.0),
            content=f'Generic decoy content number {index} about nothing special.',
        )

    await _seed_file_and_chunk(
        db_session,
        vault_pk=vault_pk,
        source_path='notes/target-unique.md',
        embedding=_basis_vector(0.0, 1.0),
        content='This note documents FunctionXyzUnique and its behavior.',
    )

    vector_only_service = _search_service(db_session, hybrid_enabled=False)
    response = await vector_only_service.search(
        vault_id=VAULT_ID,
        query='FunctionXyzUnique',
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
    assert 'notes/target-unique.md' not in result_paths
    assert len(result_paths) == 3


async def test_chunk_matched_by_both_legs_outranks_single_leg_candidates(
    db_session: AsyncSession,
) -> None:
    """A chunk that scores reasonably on both vector similarity and lexical
    match should accumulate RRF score from both legs and rank above a chunk
    that only wins on vector similarity alone (RRF's k=60 keeps per-list
    contributions close together, so appearing in two lists roughly doubles
    the score versus a single strong single-list placement).
    """
    vault_pk = await _seed_vault(db_session)

    # Best vector match, but does not contain the search term at all.
    await _seed_file_and_chunk(
        db_session,
        vault_pk=vault_pk,
        source_path='notes/vector-only.md',
        embedding=_basis_vector(1.0, 0.0),
        content='Generic content about nothing related to the query term.',
    )

    # Mid-range vector similarity and matches the lexical query.
    await _seed_file_and_chunk(
        db_session,
        vault_pk=vault_pk,
        source_path='notes/both-legs.md',
        embedding=_basis_vector(0.5, 0.5),
        content='This chunk mentions UniqueTermXyz in its body once.',
    )

    # Orthogonal to the query vector (worst cosine score) but also matches
    # the lexical query -- included so the "both legs" chunk is competing
    # against real single-leg candidates on each side.
    await _seed_file_and_chunk(
        db_session,
        vault_pk=vault_pk,
        source_path='notes/lexical-only.md',
        embedding=_basis_vector(0.0, 1.0),
        content='This chunk mentions UniqueTermXyz in its body too.',
    )

    hybrid_service = _search_service(db_session, hybrid_enabled=True)
    response = await hybrid_service.search(
        vault_id=VAULT_ID,
        query='UniqueTermXyz',
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
    assert set(result_paths) == {
        'notes/vector-only.md',
        'notes/both-legs.md',
        'notes/lexical-only.md',
    }
    both_legs_rank = result_paths.index('notes/both-legs.md')
    vector_only_rank = result_paths.index('notes/vector-only.md')
    assert both_legs_rank < vector_only_rank
    # The dual-leg chunk should place in the top 2 regardless of how ties
    # between the two lexical-matching chunks break within Postgres.
    assert both_legs_rank <= 1
