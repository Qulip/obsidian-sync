import asyncio
from logging.config import fileConfig

from sqlalchemy import Connection, pool, text
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from obsidian_sync.core.config import Settings, normalize_async_database_url
from obsidian_sync.db import models  # noqa: F401
from obsidian_sync.db.base import DB_SCHEMA, Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

PLACEHOLDER_DATABASE_URL = 'postgresql+asyncpg://user:pass@localhost/obsidian_sync'


def get_database_url() -> str:
    settings = Settings()
    if settings.database_url:
        return settings.database_url

    configured_url = config.get_main_option('sqlalchemy.url')
    if not configured_url or configured_url == PLACEHOLDER_DATABASE_URL:
        raise RuntimeError(
            'Set OBSIDIAN_SYNC_DATABASE_URL, OBSIDIAN_POSTGRESQL_URL, or '
            'DATABASE_URL before running Alembic.'
        )
    return normalize_async_database_url(configured_url)


def run_migrations_offline() -> None:
    context.configure(
        url=get_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={'paramstyle': 'named'},
        include_schemas=True,
        version_table_schema=DB_SCHEMA,
    )

    with context.begin_transaction():
        context.execute(f'CREATE SCHEMA IF NOT EXISTS {DB_SCHEMA}')
        context.execute('CREATE EXTENSION IF NOT EXISTS vector')
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS {DB_SCHEMA}'))
    connection.execute(text('CREATE EXTENSION IF NOT EXISTS vector'))
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_schemas=True,
        version_table_schema=DB_SCHEMA,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    config.set_main_option('sqlalchemy.url', get_database_url())
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix='sqlalchemy.',
        poolclass=pool.NullPool,
    )

    async with connectable.begin() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
