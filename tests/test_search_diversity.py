"""Integration tests for per-source result diversity and expanded lexical
scope in hybrid search.

These exercise KnowledgeSearchService.search directly against a real
PostgreSQL database (see tests/conftest.py), following the fixture pattern
used by tests/test_search_hybrid.py: deterministic 2D basis embeddings on
the (e0, e1) plane so cosine similarity to the query vector (always e0) can
be reasoned about by hand, combined with a stubbed OllamaClient.
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

VAULT_ID = 'diversity-vault'
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


def _stub_ollama_client(*, query_vector: list[float] | None = None) -> OllamaClient:
    client = AsyncMock(spec=OllamaClient)
    client.embed.return_value = query_vector or _query_vector()
    return client


def _search_service(
    db_session: AsyncSession,
    *,
    per_source_limit: int | None = None,
    query_vector: list[float] | None = None,
) -> KnowledgeSearchService:
    settings = get_settings()
    if per_source_limit is not None:
        settings = settings.model_copy(
            update={'search_per_source_limit': per_source_limit}
        )
    return KnowledgeSearchService(
        repository=SearchRepository(db_session),
        ollama_client=_stub_ollama_client(query_vector=query_vector),
        settings=settings,
    )


async def _seed_vault(session: AsyncSession, name: str = 'Diversity Vault') -> int:
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
    chunk_index: int,
    content_hash: str,
    embedding: list[float],
    content: str,
    title: str | None = None,
    tags: list[str] | None = None,
) -> None:
    session.add(
        KnowledgeChunk(
            vault_pk=vault_pk,
            vault_id=VAULT_ID,
            source_path=source_path,
            chunk_index=chunk_index,
            title=title or f'Chunk {chunk_index} of {source_path}',
            content=content,
            content_hash=content_hash,
            embedding=embedding,
            tags=tags,
        )
    )
    await session.flush()


async def _seed_file_and_chunk(
    session: AsyncSession,
    *,
    vault_pk: int,
    source_path: str,
    chunk_index: int,
    embedding: list[float],
    content: str,
    title: str | None = None,
    tags: list[str] | None = None,
) -> None:
    """Seed a single-chunk file: one VaultFile row plus one matching chunk.

    Only valid when `source_path` is seeded exactly once per test -- a real
    VaultFile is unique per (vault_id, source_path), so a file with multiple
    chunks must share one content_hash across all its chunks (see
    _seed_multi_chunk_file) instead of calling this per chunk.
    """
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
        chunk_index=chunk_index,
        content_hash=content_hash,
        embedding=embedding,
        content=content,
        title=title,
        tags=tags,
    )


async def _seed_multi_chunk_file(
    session: AsyncSession,
    *,
    vault_pk: int,
    source_path: str,
    chunks: list[tuple[float, float]],
) -> None:
    """Seed one VaultFile with several chunks sharing its content_hash.

    Mirrors real reindexing: a single file produces multiple knowledge_chunks
    rows, all pointing at the same vault_files.content_hash so the search
    join in SearchRepository matches every one of them.
    """
    content_hash = f'hash-{source_path}'
    await _seed_file(
        session,
        vault_pk=vault_pk,
        source_path=source_path,
        content_hash=content_hash,
    )
    for index, (a, b) in enumerate(chunks):
        await _seed_chunk(
            session,
            vault_pk=vault_pk,
            source_path=source_path,
            chunk_index=index,
            content_hash=content_hash,
            embedding=_basis_vector(a, b),
            content=f'{source_path} content variant {index}.',
        )


async def test_per_source_cap_limits_same_file_chunks_and_backfills_other_source(
    db_session: AsyncSession,
) -> None:
    """One file dominates the top vector matches with 3 similar chunks; a
    second file's single chunk is the next best match. With the default
    search_per_source_limit=2, top_k=3 should return at most 2 chunks from
    the dominant file and backfill the third slot with the other file's
    chunk instead of returning a third chunk from the same file.
    """
    vault_pk = await _seed_vault(db_session)

    await _seed_multi_chunk_file(
        db_session,
        vault_pk=vault_pk,
        source_path='notes/dominant-file.md',
        chunks=[(1.0, 0.0), (0.9, 0.1), (0.8, 0.2)],
    )

    await _seed_file_and_chunk(
        db_session,
        vault_pk=vault_pk,
        source_path='notes/other-file.md',
        chunk_index=0,
        embedding=_basis_vector(0.5, 0.5),
        content='Other file content, a weaker vector match.',
    )

    service = _search_service(db_session)
    response = await service.search(
        vault_id=VAULT_ID,
        query='diversity query',
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
    assert len(result_paths) == 3
    assert result_paths.count('notes/dominant-file.md') == 2
    assert result_paths.count('notes/other-file.md') == 1


async def test_per_source_limit_zero_disables_cap(
    db_session: AsyncSession,
) -> None:
    """search_per_source_limit=0 must restore the pre-cap behavior: all
    top_k slots can come from the same source_path.
    """
    vault_pk = await _seed_vault(db_session)

    await _seed_multi_chunk_file(
        db_session,
        vault_pk=vault_pk,
        source_path='notes/dominant-file.md',
        chunks=[(1.0, 0.0), (0.9, 0.1), (0.8, 0.2)],
    )

    await _seed_file_and_chunk(
        db_session,
        vault_pk=vault_pk,
        source_path='notes/other-file.md',
        chunk_index=0,
        embedding=_basis_vector(0.5, 0.5),
        content='Other file content, a weaker vector match.',
    )

    service = _search_service(db_session, per_source_limit=0)
    response = await service.search(
        vault_id=VAULT_ID,
        query='diversity query',
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
    assert len(result_paths) == 3
    assert result_paths.count('notes/dominant-file.md') == 3


async def test_lexical_search_matches_source_path_filename_token(
    db_session: AsyncSession,
) -> None:
    """content_tsv now indexes source_path (with '/' and '.' replaced by
    spaces so path segments and the filename stem tokenize separately), so
    a query for a filename fragment should match via the lexical leg even
    when the chunk's embedding is near-orthogonal to the query vector.
    """
    vault_pk = await _seed_vault(db_session)

    # Decoys with a perfect cosine match fill every vector-only top_k slot.
    for index in range(3):
        await _seed_file_and_chunk(
            db_session,
            vault_pk=vault_pk,
            source_path=f'notes/decoy-{index}.md',
            chunk_index=0,
            embedding=_basis_vector(1.0, 0.0),
            content=f'Generic decoy content number {index} about nothing special.',
        )

    await _seed_file_and_chunk(
        db_session,
        vault_pk=vault_pk,
        source_path='projects/alpha-notes.md',
        chunk_index=0,
        embedding=_basis_vector(0.0, 1.0),
        content='Body text unrelated to the search term.',
    )

    service = _search_service(db_session)
    response = await service.search(
        vault_id=VAULT_ID,
        query='alpha-notes',
        filters=None,
        top_k=4,
        project=None,
        domain=None,
        min_score=None,
        token_id=None,
        client_ip=None,
        user_agent=None,
    )

    results_by_path = {result.source_path: result for result in response.results}
    assert 'projects/alpha-notes.md' in results_by_path
    assert results_by_path['projects/alpha-notes.md'].matched_by in ('lexical', 'both')


async def test_lexical_search_matches_tag_string(
    db_session: AsyncSession,
) -> None:
    """content_tsv now indexes tags (via the immutable_tags_text wrapper
    around array_to_string), so a query matching a tag value should surface
    the chunk via the lexical leg even with a near-orthogonal embedding.
    """
    vault_pk = await _seed_vault(db_session)

    for index in range(3):
        await _seed_file_and_chunk(
            db_session,
            vault_pk=vault_pk,
            source_path=f'notes/decoy-{index}.md',
            chunk_index=0,
            embedding=_basis_vector(1.0, 0.0),
            content=f'Generic decoy content number {index} about nothing special.',
        )

    await _seed_file_and_chunk(
        db_session,
        vault_pk=vault_pk,
        source_path='notes/tagged-note.md',
        chunk_index=0,
        embedding=_basis_vector(0.0, 1.0),
        content='Body text unrelated to the search term.',
        tags=['zzzuniquetag'],
    )

    service = _search_service(db_session)
    response = await service.search(
        vault_id=VAULT_ID,
        query='zzzuniquetag',
        filters=None,
        top_k=4,
        project=None,
        domain=None,
        min_score=None,
        token_id=None,
        client_ip=None,
        user_agent=None,
    )

    results_by_path = {result.source_path: result for result in response.results}
    assert 'notes/tagged-note.md' in results_by_path
    assert results_by_path['notes/tagged-note.md'].matched_by in ('lexical', 'both')
