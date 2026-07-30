"""Shared pytest fixtures for API and integration tests.

Environment variables are set at import time (before the application package is
imported) so the lru_cached settings pick up the dedicated test database and
temporary storage roots instead of the real configured targets.
"""

import asyncio
import hashlib
import os
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import asyncpg
import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
TEST_DB_NAME = 'obsidian_sync_test'


def _read_real_database_url() -> str:
    for key in ('OBSIDIAN_SYNC_DATABASE_URL', 'OBSIDIAN_POSTGRESQL_URL'):
        value = os.environ.get(key)
        if value:
            return value
    env_file = _REPO_ROOT / '.env'
    for line in env_file.read_text(encoding='utf-8').splitlines():
        if line.startswith('OBSIDIAN_POSTGRESQL_URL='):
            return line.split('=', 1)[1].strip()
    raise RuntimeError('no database url configured for tests')


def _swap_database(url: str, database: str) -> str:
    base, _, _ = url.rpartition('/')
    return f'{base}/{database}'


def _asyncpg_dsn(async_url: str) -> str:
    return async_url.replace('postgresql+asyncpg://', 'postgresql://', 1)


_REAL_URL = _read_real_database_url()
TEST_ASYNC_URL = _swap_database(_REAL_URL, TEST_DB_NAME)
_ADMIN_DSN = _asyncpg_dsn(_swap_database(_REAL_URL, 'postgres'))
_TEST_DSN = _asyncpg_dsn(TEST_ASYNC_URL)

_TMP_ROOT = Path(tempfile.mkdtemp(prefix='obsidian-sync-tests-'))
os.environ['OBSIDIAN_SYNC_DATABASE_URL'] = TEST_ASYNC_URL
os.environ['OBSIDIAN_SYNC_VAULT_STORAGE_ROOT'] = str(_TMP_ROOT / 'vaults')
os.environ['OBSIDIAN_SYNC_VAULT_ARCHIVE_ROOT'] = str(_TMP_ROOT / 'archives')
os.environ['OBSIDIAN_SYNC_API_TOKEN'] = 'admin-secret'


async def _create_test_database() -> None:
    conn = await asyncpg.connect(dsn=_ADMIN_DSN)
    try:
        await conn.execute(
            'SELECT pg_terminate_backend(pid) FROM pg_stat_activity '
            'WHERE datname = $1 AND pid <> pg_backend_pid()',
            TEST_DB_NAME,
        )
        await conn.execute(f'DROP DATABASE IF EXISTS {TEST_DB_NAME}')
        await conn.execute(f'CREATE DATABASE {TEST_DB_NAME}')
    finally:
        await conn.close()


@pytest.fixture(scope='session')
def _database() -> Iterator[None]:
    asyncio.run(_create_test_database())

    from alembic.config import Config

    from alembic import command

    config = Config(str(_REPO_ROOT / 'alembic.ini'))
    config.set_main_option('script_location', str(_REPO_ROOT / 'alembic'))
    command.upgrade(config, 'head')
    yield


async def _truncate_all() -> None:
    conn = await asyncpg.connect(dsn=_TEST_DSN)
    try:
        rows = await conn.fetch(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'obsidian' "
            "AND tablename <> 'alembic_version'"
        )
        tables = ', '.join(f'obsidian.{row["tablename"]}' for row in rows)
        if tables:
            await conn.execute(f'TRUNCATE {tables} RESTART IDENTITY CASCADE')
    finally:
        await conn.close()


@pytest.fixture
def clean_db(_database: None) -> None:
    asyncio.run(_truncate_all())


@pytest.fixture
def db_fetch(clean_db: None) -> Callable[..., list[asyncpg.Record]]:
    def _fetch(query: str, *args: Any) -> list[asyncpg.Record]:
        async def run() -> list[asyncpg.Record]:
            conn = await asyncpg.connect(dsn=_TEST_DSN)
            try:
                return list(await conn.fetch(query, *args))
            finally:
                await conn.close()

        return asyncio.run(run())

    return _fetch


@pytest.fixture
def app_client(clean_db: None) -> Iterator[Any]:
    from fastapi.testclient import TestClient

    from obsidian_sync.app import create_app
    from obsidian_sync.core.config import get_settings

    get_settings.cache_clear()
    settings = get_settings().model_copy(update={'post_sync_indexing_enabled': False})
    app = create_app(settings)
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as client:
        yield client


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


@pytest.fixture
def api_token(clean_db: None) -> str:
    raw = 'test-bearer-token'

    async def insert() -> None:
        conn = await asyncpg.connect(dsn=_TEST_DSN)
        try:
            # allow_overwrite=true: this shared fixture token backs broad
            # API test coverage, including MCP overwrite=True scenarios in
            # test_mcp_vault_sync.py. Token-scoped permission denial has its
            # own dedicated coverage rather than being exercised here.
            await conn.execute(
                'INSERT INTO obsidian.api_tokens '
                '(name, token_hash, allow_overwrite) VALUES ($1, $2, true)',
                'test-token',
                _hash_token(raw),
            )
        finally:
            await conn.close()

    asyncio.run(insert())
    return raw


@pytest.fixture
def auth_headers(api_token: str) -> dict[str, str]:
    return {'Authorization': f'Bearer {api_token}'}


@pytest.fixture
def vault_id(app_client: Any, auth_headers: dict[str, str]) -> str:
    vid = 'testvault'
    response = app_client.post(
        '/vaults',
        json={'vault_id': vid, 'name': 'Test Vault'},
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    return vid
