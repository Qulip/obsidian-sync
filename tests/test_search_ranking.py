"""Ranking regression tests for vector search.

These pin down the ordering and scoring behavior of
SearchRepository.search_chunks (pgvector cosine similarity) and
KnowledgeSearchService.search against a real PostgreSQL database (see
tests/conftest.py). Fixture embeddings are deterministic 1024-dimensional
vectors built from two basis components so cosine similarity to the query
vector can be computed by hand and asserted with pytest.approx.

Query vector is always the first basis vector, e0 = (1, 0). Each fixture
chunk embedding is (a, b) on the (e0, e1) plane (all other dimensions are
zero), so cosine_similarity(query, chunk) = a / sqrt(a**2 + b**2) -- the
value pgvector's `<=>` cosine distance operator (score = 1 - distance)
should reproduce.
"""

import math
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from obsidian_sync.clients.ollama import OllamaClient
from obsidian_sync.core.config import get_settings
from obsidian_sync.db.models import KnowledgeChunk, Vault, VaultFile
from obsidian_sync.db.session import build_async_engine, build_sessionmaker
from obsidian_sync.domain.search import SearchFilters
from obsidian_sync.repositories.search import SearchRepository, SearchResultRecord
from obsidian_sync.services.search import KnowledgeSearchService

VAULT_ID = 'ranking-vault'
EMBEDDING_DIMENSIONS = 1024

# (source_path, a, b) -- ordered from most to least similar to the query.
# Expected cosine similarity: a / sqrt(a**2 + b**2).
RANKING_FIXTURES: tuple[tuple[str, float, float], ...] = (
    ('notes/rank-1-exact.md', 1.0, 0.0),
    ('notes/rank-2-close.md', 3.0, 1.0),
    ('notes/rank-3-mixed.md', 1.0, 1.0),
    ('notes/rank-4-far.md', 1.0, 3.0),
    ('notes/rank-5-orthogonal.md', 0.0, 1.0),
)


def _basis_vector(a: float, b: float) -> list[float]:
    vector = [0.0] * EMBEDDING_DIMENSIONS
    vector[0] = a
    vector[1] = b
    return vector


def _query_vector() -> list[float]:
    return _basis_vector(1.0, 0.0)


def _expected_score(a: float, b: float) -> float:
    return a / math.sqrt(a * a + b * b)


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


async def _search_service(db_session: AsyncSession) -> KnowledgeSearchService:
    return KnowledgeSearchService(
        repository=SearchRepository(db_session),
        ollama_client=_stub_ollama_client(),
        settings=get_settings(),
    )


async def _seed_vault(session: AsyncSession, name: str = 'Ranking Vault') -> int:
    vault = Vault(vault_id=VAULT_ID, name=name)
    session.add(vault)
    await session.flush()
    return vault.id


async def _seed_file(
    session: AsyncSession,
    *,
    vault_pk: int,
    source_path: str,
    content_hash: str,
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
    chunk_index: int,
    content_hash: str,
    embedding: list[float],
    project: str | None = None,
    domain: str | None = None,
    tags: list[str] | None = None,
) -> None:
    session.add(
        KnowledgeChunk(
            vault_pk=vault_pk,
            vault_id=VAULT_ID,
            source_path=source_path,
            chunk_index=chunk_index,
            title=f'Chunk {chunk_index} of {source_path}',
            content=f'Content body for {source_path} chunk {chunk_index}.',
            content_hash=content_hash,
            embedding=embedding,
            project=project,
            domain=domain,
            tags=tags,
        )
    )
    await session.flush()


async def _seed_ranking_fixtures(session: AsyncSession, vault_pk: int) -> None:
    for source_path, a, b in RANKING_FIXTURES:
        content_hash = f'hash-{source_path}'
        await _seed_file(
            session,
            vault_pk=vault_pk,
            source_path=source_path,
            content_hash=content_hash,
        )
        await _seed_chunk(
            session,
            vault_pk=vault_pk,
            source_path=source_path,
            chunk_index=0,
            content_hash=content_hash,
            embedding=_basis_vector(a, b),
        )


async def _search_chunks(
    session: AsyncSession, *, filters: SearchFilters | None = None, top_k: int = 10
) -> list[SearchResultRecord]:
    repository = SearchRepository(session)
    return await repository.search_chunks(
        vault_id=VAULT_ID,
        query_embedding=_query_vector(),
        filters=filters or SearchFilters(),
        top_k=top_k,
    )


async def test_results_are_ordered_by_cosine_similarity_descending(
    db_session: AsyncSession,
) -> None:
    vault_pk = await _seed_vault(db_session)
    await _seed_ranking_fixtures(db_session, vault_pk)

    records = await _search_chunks(db_session, top_k=10)

    expected_order = [source_path for source_path, _a, _b in RANKING_FIXTURES]
    assert [record.source_path for record in records] == expected_order


async def test_scores_match_expected_cosine_similarity(
    db_session: AsyncSession,
) -> None:
    vault_pk = await _seed_vault(db_session)
    await _seed_ranking_fixtures(db_session, vault_pk)

    records = await _search_chunks(db_session, top_k=10)
    scores_by_path = {record.source_path: record.score for record in records}

    for source_path, a, b in RANKING_FIXTURES:
        assert scores_by_path[source_path] == pytest.approx(
            _expected_score(a, b), abs=1e-3
        )


async def test_top_k_limits_results_to_requested_count(
    db_session: AsyncSession,
) -> None:
    vault_pk = await _seed_vault(db_session)
    await _seed_ranking_fixtures(db_session, vault_pk)

    records = await _search_chunks(db_session, top_k=2)

    assert [record.source_path for record in records] == [
        'notes/rank-1-exact.md',
        'notes/rank-2-close.md',
    ]


async def test_service_search_assigns_rank_matching_similarity_order(
    db_session: AsyncSession,
) -> None:
    vault_pk = await _seed_vault(db_session)
    await _seed_ranking_fixtures(db_session, vault_pk)

    service = await _search_service(db_session)
    response = await service.search(
        vault_id=VAULT_ID,
        query='ranking query',
        filters=None,
        top_k=None,
        project=None,
        domain=None,
        min_score=None,
        token_id=None,
        client_ip=None,
        user_agent=None,
    )

    # normalize_top_k defaults to 5, matching all seeded fixtures.
    assert [result.source_path for result in response.results] == [
        source_path for source_path, _a, _b in RANKING_FIXTURES
    ]
    for result, (_source_path, a, b) in zip(
        response.results, RANKING_FIXTURES, strict=True
    ):
        assert result.score == pytest.approx(_expected_score(a, b), abs=1e-3)
    assert [result.rank for result in response.results] == [1, 2, 3, 4, 5]


async def test_project_filter_returns_only_matching_chunks(
    db_session: AsyncSession,
) -> None:
    vault_pk = await _seed_vault(db_session)
    for source_path, project in (
        ('notes/proj-a.md', 'proj-a'),
        ('notes/proj-b.md', 'proj-b'),
        ('notes/proj-none.md', None),
    ):
        content_hash = f'hash-{source_path}'
        await _seed_file(
            db_session,
            vault_pk=vault_pk,
            source_path=source_path,
            content_hash=content_hash,
        )
        await _seed_chunk(
            db_session,
            vault_pk=vault_pk,
            source_path=source_path,
            chunk_index=0,
            content_hash=content_hash,
            embedding=_query_vector(),
            project=project,
        )

    records = await _search_chunks(
        db_session, filters=SearchFilters(project='proj-a'), top_k=10
    )

    assert [record.source_path for record in records] == ['notes/proj-a.md']


async def test_domain_filter_returns_only_matching_chunks(
    db_session: AsyncSession,
) -> None:
    vault_pk = await _seed_vault(db_session)
    for source_path, domain in (
        ('notes/domain-a.md', 'backend'),
        ('notes/domain-b.md', 'frontend'),
        ('notes/domain-none.md', None),
    ):
        content_hash = f'hash-{source_path}'
        await _seed_file(
            db_session,
            vault_pk=vault_pk,
            source_path=source_path,
            content_hash=content_hash,
        )
        await _seed_chunk(
            db_session,
            vault_pk=vault_pk,
            source_path=source_path,
            chunk_index=0,
            content_hash=content_hash,
            embedding=_query_vector(),
            domain=domain,
        )

    records = await _search_chunks(
        db_session, filters=SearchFilters(domain='backend'), top_k=10
    )

    assert [record.source_path for record in records] == ['notes/domain-a.md']


async def test_tags_filter_returns_only_chunks_with_overlapping_tag(
    db_session: AsyncSession,
) -> None:
    vault_pk = await _seed_vault(db_session)
    for source_path, tags in (
        ('notes/tag-urgent.md', ['urgent', 'billing']),
        ('notes/tag-other.md', ['reference']),
        ('notes/tag-none.md', None),
    ):
        content_hash = f'hash-{source_path}'
        await _seed_file(
            db_session,
            vault_pk=vault_pk,
            source_path=source_path,
            content_hash=content_hash,
        )
        await _seed_chunk(
            db_session,
            vault_pk=vault_pk,
            source_path=source_path,
            chunk_index=0,
            content_hash=content_hash,
            embedding=_query_vector(),
            tags=tags,
        )

    records = await _search_chunks(
        db_session, filters=SearchFilters(tags=('urgent',)), top_k=10
    )

    assert [record.source_path for record in records] == ['notes/tag-urgent.md']


async def test_multiple_chunks_from_same_source_path_are_all_returned(
    db_session: AsyncSession,
) -> None:
    """Documents current behavior: no per-document diversity/dedup is applied.

    If two chunks from the same file both rank highly, both are returned as
    separate results. This is intentional for now -- diversity or per-source
    capping is not implemented and may change if hybrid search or a reranking
    stage is added later.
    """
    vault_pk = await _seed_vault(db_session)
    source_path = 'notes/multi-chunk.md'
    content_hash = f'hash-{source_path}'
    await _seed_file(
        db_session,
        vault_pk=vault_pk,
        source_path=source_path,
        content_hash=content_hash,
    )
    await _seed_chunk(
        db_session,
        vault_pk=vault_pk,
        source_path=source_path,
        chunk_index=0,
        content_hash=content_hash,
        embedding=_basis_vector(1.0, 0.0),
    )
    await _seed_chunk(
        db_session,
        vault_pk=vault_pk,
        source_path=source_path,
        chunk_index=1,
        content_hash=content_hash,
        embedding=_basis_vector(3.0, 1.0),
    )

    records = await _search_chunks(db_session, top_k=10)

    assert len(records) == 2
    assert all(record.source_path == source_path for record in records)
