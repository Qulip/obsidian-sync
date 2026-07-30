"""Tests for the revision GC script: archive population + version pruning.

These exercise scripts/cleanup_deleted_files.py directly against a real
PostgreSQL database (see tests/conftest.py). The script lives outside the
`obsidian_sync` package, so it is imported as the `scripts` namespace
package, which pytest can resolve because the repo root is on sys.path
(tests/ is itself a package, so pytest adds its parent -- the repo root --
to sys.path during collection).
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from obsidian_sync.core.config import get_settings
from obsidian_sync.db.models import (
    ArchivedKnowledgeChunk,
    ArchivedVaultFile,
    KnowledgeChunk,
    Vault,
    VaultFile,
    VaultFileVersion,
)
from obsidian_sync.db.session import build_async_engine, build_sessionmaker
from scripts.cleanup_deleted_files import (
    ARCHIVE_REASON_SOFT_DELETE_EXPIRED,
    CleanupOptions,
    _archive_expired_files,
    _prune_versions,
)

VAULT_ID = 'gc-vault'
RETENTION_DAYS = 7
VERSION_RETENTION_DAYS = 90
EMBEDDING_DIMENSIONS = 1024


def _embedding(seed: float = 1.0) -> list[float]:
    return [seed] + [0.0] * (EMBEDDING_DIMENSIONS - 1)


def _naive_utc_now() -> datetime:
    # vault_files.deleted_at and vault_file_versions.created_at are TIMESTAMP
    # WITHOUT TIME ZONE columns, so seeded values must be naive to match what
    # the ORM's server-generated defaults (func.now()) produce.
    return datetime.now(UTC).replace(tzinfo=None)


@pytest.fixture
async def db_session(clean_db: None) -> AsyncIterator[AsyncSession]:
    settings = get_settings()
    assert settings.database_url is not None
    engine = build_async_engine(settings.database_url)
    sessionmaker = build_sessionmaker(engine)
    async with sessionmaker() as session:
        yield session
    await engine.dispose()


def _options(
    tmp_path: Path,
    *,
    dry_run: bool,
    retention_days: int = RETENTION_DAYS,
    version_retention_days: int = VERSION_RETENTION_DAYS,
) -> CleanupOptions:
    return CleanupOptions(
        vault_root=tmp_path / 'vaults',
        archive_root=tmp_path / 'archives',
        retention_days=retention_days,
        version_retention_days=version_retention_days,
        dry_run=dry_run,
    )


async def _seed_vault(session: AsyncSession) -> int:
    vault = Vault(vault_id=VAULT_ID, name='GC Vault')
    session.add(vault)
    await session.flush()
    return int(vault.id)


async def _seed_soft_deleted_file(
    session: AsyncSession,
    *,
    vault_pk: int,
    source_path: str,
    deleted_at: datetime,
) -> int:
    row = VaultFile(
        vault_pk=vault_pk,
        vault_id=VAULT_ID,
        source_path=source_path,
        content_hash='a' * 64,
        size_bytes=10,
        revision=2,
        deleted=True,
        deleted_at=deleted_at,
        status='archived',
        index_status='skipped',
    )
    session.add(row)
    await session.flush()
    return int(row.id)


async def _seed_chunk(session: AsyncSession, *, vault_pk: int, source_path: str) -> int:
    chunk = KnowledgeChunk(
        vault_pk=vault_pk,
        vault_id=VAULT_ID,
        source_path=source_path,
        chunk_index=0,
        content='stale chunk content',
        content_hash='b' * 64,
        embedding=_embedding(),
    )
    session.add(chunk)
    await session.flush()
    return int(chunk.id)


async def _count(session: AsyncSession, model: type, **filters: object) -> int:
    stmt = select(func.count()).select_from(model)
    for key, value in filters.items():
        stmt = stmt.where(getattr(model, key) == value)
    result = await session.execute(stmt)
    return int(result.scalar_one())


async def test_archive_moves_expired_file_and_purges_live_rows(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    vault_pk = await _seed_vault(db_session)
    source_path = 'notes/expired.md'
    deleted_at = _naive_utc_now() - timedelta(days=RETENTION_DAYS + 1)
    file_id = await _seed_soft_deleted_file(
        db_session, vault_pk=vault_pk, source_path=source_path, deleted_at=deleted_at
    )
    chunk_id = await _seed_chunk(db_session, vault_pk=vault_pk, source_path=source_path)
    await db_session.commit()

    options = _options(tmp_path, dry_run=False)
    original = options.vault_root / VAULT_ID / source_path
    original.parent.mkdir(parents=True, exist_ok=True)
    original.write_text('expired content')

    summary = await _archive_expired_files(db_session, options)

    assert summary['archived_count'] == 1
    assert summary['moved_count'] == 1
    assert summary['missing_count'] == 0

    assert await _count(db_session, VaultFile, id=file_id) == 0
    assert await _count(db_session, KnowledgeChunk, id=chunk_id) == 0

    archived_file_result = await db_session.execute(
        select(ArchivedVaultFile).where(ArchivedVaultFile.original_id == file_id)
    )
    archived_file = archived_file_result.scalar_one()
    assert archived_file.vault_id == VAULT_ID
    assert archived_file.source_path == source_path
    assert archived_file.archived_reason == ARCHIVE_REASON_SOFT_DELETE_EXPIRED
    assert archived_file.raw_record is not None
    assert archived_file.raw_record['id'] == file_id
    assert archived_file.raw_record['deleted'] is True

    archived_chunk_result = await db_session.execute(
        select(ArchivedKnowledgeChunk).where(
            ArchivedKnowledgeChunk.original_id == chunk_id
        )
    )
    archived_chunk = archived_chunk_result.scalar_one()
    assert archived_chunk.source_path == source_path
    assert archived_chunk.archived_reason == ARCHIVE_REASON_SOFT_DELETE_EXPIRED

    assert not original.exists()
    destination = options.archive_root / VAULT_ID / source_path
    assert destination.read_text() == 'expired content'


async def test_archive_skips_file_still_within_retention(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    vault_pk = await _seed_vault(db_session)
    source_path = 'notes/recent.md'
    deleted_at = _naive_utc_now() - timedelta(days=1)
    file_id = await _seed_soft_deleted_file(
        db_session, vault_pk=vault_pk, source_path=source_path, deleted_at=deleted_at
    )
    await db_session.commit()

    options = _options(tmp_path, dry_run=False)
    original = options.vault_root / VAULT_ID / source_path
    original.parent.mkdir(parents=True, exist_ok=True)
    original.write_text('recent content')

    summary = await _archive_expired_files(db_session, options)

    assert summary['archived_count'] == 0
    assert await _count(db_session, VaultFile, id=file_id) == 1
    assert await _count(db_session, ArchivedVaultFile) == 0
    assert original.exists()


async def test_prune_versions_always_keeps_latest_revision(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    vault_pk = await _seed_vault(db_session)
    source_path = 'notes/history.md'
    old_created_at = _naive_utc_now() - timedelta(days=VERSION_RETENTION_DAYS + 10)
    recent_created_at = _naive_utc_now() - timedelta(days=1)

    # revision 1: old and not latest -> eligible for pruning.
    db_session.add(
        VaultFileVersion(
            vault_pk=vault_pk,
            vault_id=VAULT_ID,
            source_path=source_path,
            revision=1,
            content_hash='a' * 64,
            content='v1',
            event_type='CREATE',
            created_at=old_created_at,
        )
    )
    # revision 2: not old enough -> must survive despite not being latest.
    db_session.add(
        VaultFileVersion(
            vault_pk=vault_pk,
            vault_id=VAULT_ID,
            source_path=source_path,
            revision=2,
            content_hash='b' * 64,
            content='v2',
            event_type='UPDATE',
            created_at=recent_created_at,
        )
    )
    # revision 3: old but is the latest revision for this path -> must survive.
    db_session.add(
        VaultFileVersion(
            vault_pk=vault_pk,
            vault_id=VAULT_ID,
            source_path=source_path,
            revision=3,
            content_hash='c' * 64,
            content='v3',
            event_type='UPDATE',
            created_at=old_created_at,
        )
    )
    await db_session.commit()

    options = _options(tmp_path, dry_run=False)
    summary = await _prune_versions(db_session, options)

    assert summary['pruned_count'] == 1
    assert summary['pruned'] == [f'{VAULT_ID}/{source_path}#1']

    remaining_result = await db_session.execute(
        select(VaultFileVersion.revision)
        .where(
            VaultFileVersion.vault_id == VAULT_ID,
            VaultFileVersion.source_path == source_path,
        )
        .order_by(VaultFileVersion.revision)
    )
    remaining_revisions = [row[0] for row in remaining_result.all()]
    assert remaining_revisions == [2, 3]


async def test_dry_run_makes_no_changes(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    vault_pk = await _seed_vault(db_session)
    source_path = 'notes/expired-dry-run.md'
    deleted_at = _naive_utc_now() - timedelta(days=RETENTION_DAYS + 1)
    file_id = await _seed_soft_deleted_file(
        db_session, vault_pk=vault_pk, source_path=source_path, deleted_at=deleted_at
    )
    await _seed_chunk(db_session, vault_pk=vault_pk, source_path=source_path)

    old_created_at = _naive_utc_now() - timedelta(days=VERSION_RETENTION_DAYS + 10)
    db_session.add(
        VaultFileVersion(
            vault_pk=vault_pk,
            vault_id=VAULT_ID,
            source_path=source_path,
            revision=1,
            content_hash='d' * 64,
            content='v1',
            event_type='CREATE',
            created_at=old_created_at,
        )
    )
    db_session.add(
        VaultFileVersion(
            vault_pk=vault_pk,
            vault_id=VAULT_ID,
            source_path=source_path,
            revision=2,
            content_hash='e' * 64,
            content='v2',
            event_type='UPDATE',
            created_at=old_created_at,
        )
    )
    await db_session.commit()

    options = _options(tmp_path, dry_run=True)
    original = options.vault_root / VAULT_ID / source_path
    original.parent.mkdir(parents=True, exist_ok=True)
    original.write_text('expired content')

    archive_summary = await _archive_expired_files(db_session, options)
    prune_summary = await _prune_versions(db_session, options)

    # Reporting still reflects what *would* happen.
    assert archive_summary['archived_count'] == 1
    assert prune_summary['pruned_count'] == 1

    # But nothing actually changed.
    assert await _count(db_session, VaultFile, id=file_id) == 1
    assert await _count(db_session, KnowledgeChunk, vault_id=VAULT_ID) == 1
    assert await _count(db_session, ArchivedVaultFile) == 0
    assert await _count(db_session, ArchivedKnowledgeChunk) == 0
    assert await _count(db_session, VaultFileVersion, vault_id=VAULT_ID) == 2
    assert original.exists()
    assert not (options.archive_root / VAULT_ID / source_path).exists()
