"""Service-level tests for search feedback recording.

These exercise KnowledgeSearchService.search and SearchLogService against a
real PostgreSQL database (see tests/conftest.py), following the fixture
pattern used by tests/test_search_threshold.py: the Ollama embedding call is
stubbed via AsyncMock so no live Ollama server is required.
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
from obsidian_sync.services.search import KnowledgeSearchService, SearchLogService

VAULT_ID = 'feedback-vault'
SOURCE_PATH = 'notes/feedback.md'
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


async def _seed_vault(session: AsyncSession) -> int:
    vault = Vault(vault_id=VAULT_ID, name='Feedback Vault')
    session.add(vault)
    await session.flush()
    return vault.id


async def _seed_indexed_file(session: AsyncSession, *, vault_pk: int) -> None:
    session.add(
        VaultFile(
            vault_pk=vault_pk,
            vault_id=VAULT_ID,
            source_path=SOURCE_PATH,
            content_hash='hash-feedback',
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
            title='Feedback',
            content='Feedback content body.',
            content_hash='hash-feedback',
            embedding=_query_embedding(),
        )
    )
    await session.flush()


async def _search_service(db_session: AsyncSession) -> KnowledgeSearchService:
    return KnowledgeSearchService(
        repository=SearchRepository(db_session),
        ollama_client=_stub_ollama_client(),
        settings=get_settings(),
    )


async def _run_search(db_session: AsyncSession) -> str:
    vault_pk = await _seed_vault(db_session)
    await _seed_indexed_file(db_session, vault_pk=vault_pk)

    service = await _search_service(db_session)
    response = await service.search(
        vault_id=VAULT_ID,
        query='feedback content',
        filters=None,
        top_k=None,
        project=None,
        domain=None,
        min_score=None,
        token_id='token-1',
        client_ip='127.0.0.1',
        user_agent='pytest',
    )
    assert response.request_id
    return response.request_id


async def test_feedback_is_recorded_and_visible_in_logs(
    db_session: AsyncSession,
) -> None:
    request_id = await _run_search(db_session)

    log_service = SearchLogService(repository=SearchRepository(db_session))
    feedback_response = await log_service.record_feedback(
        request_id=request_id,
        vault_id=VAULT_ID,
        helpful=True,
        selected_source_path=SOURCE_PATH,
        selected_chunk_rank=1,
        expected_missing=False,
        comment='Exactly what I needed.',
    )

    assert feedback_response.request_id == request_id
    assert feedback_response.vault_id == VAULT_ID
    assert feedback_response.feedback_at is not None

    logs_response = await log_service.list_logs(
        vault_id=VAULT_ID,
        from_time=None,
        to_time=None,
        query=None,
        limit=10,
    )
    assert len(logs_response.logs) == 1
    log_item = logs_response.logs[0]
    assert log_item.request_id == request_id
    assert log_item.feedback_helpful is True
    assert log_item.feedback_selected_source_path == SOURCE_PATH
    assert log_item.feedback_selected_chunk_rank == 1
    assert log_item.feedback_expected_missing is False
    assert log_item.feedback_comment == 'Exactly what I needed.'
    assert log_item.feedback_at is not None


async def test_feedback_for_unknown_request_id_raises_not_found(
    db_session: AsyncSession,
) -> None:
    await _run_search(db_session)

    log_service = SearchLogService(repository=SearchRepository(db_session))
    with pytest.raises(AppError) as excinfo:
        await log_service.record_feedback(
            request_id='does-not-exist',
            vault_id=VAULT_ID,
            helpful=True,
            selected_source_path=None,
            selected_chunk_rank=None,
            expected_missing=None,
            comment=None,
        )

    assert excinfo.value.code == ErrorCode.NOT_FOUND
    assert excinfo.value.status_code == 404


async def test_feedback_with_no_fields_raises_validation_error(
    db_session: AsyncSession,
) -> None:
    request_id = await _run_search(db_session)

    log_service = SearchLogService(repository=SearchRepository(db_session))
    with pytest.raises(AppError) as excinfo:
        await log_service.record_feedback(
            request_id=request_id,
            vault_id=VAULT_ID,
            helpful=None,
            selected_source_path=None,
            selected_chunk_rank=None,
            expected_missing=None,
            comment=None,
        )

    assert excinfo.value.code == ErrorCode.VALIDATION_ERROR
    assert excinfo.value.status_code == 422
