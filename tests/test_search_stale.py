"""Integration tests for stale-chunk exclusion in SearchRepository.search_chunks.

These tests exercise the repository directly against a real PostgreSQL
database (see tests/conftest.py) instead of going through the HTTP API,
since the search flow otherwise requires an Ollama embedding call.
"""

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from obsidian_sync.core.config import get_settings
from obsidian_sync.db.models import KnowledgeChunk, Vault, VaultFile
from obsidian_sync.db.session import build_async_engine, build_sessionmaker
from obsidian_sync.domain.search import SearchFilters
from obsidian_sync.repositories.search import SearchRepository

VAULT_ID = 'stale-chunk-vault'
SOURCE_PATH = 'notes/example.md'
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


async def _seed_vault(session: AsyncSession) -> int:
    vault = Vault(vault_id=VAULT_ID, name='Stale Chunk Vault')
    session.add(vault)
    await session.flush()
    return vault.id


async def _seed_file(
    session: AsyncSession,
    *,
    vault_pk: int,
    content_hash: str,
    index_status: str = 'indexed',
    deleted: bool = False,
) -> VaultFile:
    vault_file = VaultFile(
        vault_pk=vault_pk,
        vault_id=VAULT_ID,
        source_path=SOURCE_PATH,
        content_hash=content_hash,
        index_status=index_status,
        deleted=deleted,
        revision=1,
    )
    session.add(vault_file)
    await session.flush()
    return vault_file


async def _seed_chunk(
    session: AsyncSession,
    *,
    vault_pk: int,
    content_hash: str,
) -> None:
    session.add(
        KnowledgeChunk(
            vault_pk=vault_pk,
            vault_id=VAULT_ID,
            source_path=SOURCE_PATH,
            chunk_index=0,
            title='Example',
            content='Example content body.',
            content_hash=content_hash,
            embedding=_embedding(),
        )
    )
    await session.flush()


async def _search(session: AsyncSession) -> list[str]:
    repository = SearchRepository(session)
    records = await repository.search_chunks(
        vault_id=VAULT_ID,
        query_embedding=_embedding(),
        filters=SearchFilters(),
        top_k=10,
        embedding_model=get_settings().embedding_model,
    )
    return [record.source_path for record in records]


async def test_indexed_file_with_matching_hash_is_returned(
    db_session: AsyncSession,
) -> None:
    vault_pk = await _seed_vault(db_session)
    await _seed_file(db_session, vault_pk=vault_pk, content_hash='hash-current')
    await _seed_chunk(db_session, vault_pk=vault_pk, content_hash='hash-current')

    results = await _search(db_session)

    assert results == [SOURCE_PATH]


async def test_pending_reindex_with_stale_hash_is_excluded(
    db_session: AsyncSession,
) -> None:
    vault_pk = await _seed_vault(db_session)
    await _seed_file(db_session, vault_pk=vault_pk, content_hash='hash-current')
    await _seed_chunk(db_session, vault_pk=vault_pk, content_hash='hash-current')

    assert await _search(db_session) == [SOURCE_PATH]

    # File content changed on disk: content_hash moves ahead and reindexing
    # has not completed yet, so the old chunk's content_hash is now stale.
    await db_session.execute(
        VaultFile.__table__.update()
        .where(VaultFile.vault_id == VAULT_ID, VaultFile.source_path == SOURCE_PATH)
        .values(content_hash='hash-new', index_status='pending')
    )
    await db_session.flush()

    results = await _search(db_session)

    assert results == []


async def test_deleted_file_is_excluded(db_session: AsyncSession) -> None:
    vault_pk = await _seed_vault(db_session)
    await _seed_file(db_session, vault_pk=vault_pk, content_hash='hash-current')
    await _seed_chunk(db_session, vault_pk=vault_pk, content_hash='hash-current')

    assert await _search(db_session) == [SOURCE_PATH]

    await db_session.execute(
        VaultFile.__table__.update()
        .where(VaultFile.vault_id == VAULT_ID, VaultFile.source_path == SOURCE_PATH)
        .values(deleted=True)
    )
    await db_session.flush()

    results = await _search(db_session)

    assert results == []
