from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast

from sqlalchemy import and_, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from obsidian_sync.core.config import Settings
from obsidian_sync.db.models import (
    ArchivedKnowledgeChunk,
    ArchivedVaultFile,
    KnowledgeChunk,
    VaultFile,
    VaultFileVersion,
)
from obsidian_sync.db.session import build_async_engine, build_sessionmaker
from obsidian_sync.domain.paths import safe_vault_destination

ARCHIVE_REASON_SOFT_DELETE_EXPIRED = 'soft_delete_retention_expired'


@dataclass(frozen=True, slots=True)
class CleanupOptions:
    vault_root: Path
    archive_root: Path
    retention_days: int
    version_retention_days: int
    dry_run: bool


def main() -> int:
    settings = Settings()
    options = _parse_args(settings)
    if settings.database_url is None:
        print('OBSIDIAN_SYNC_DATABASE_URL is not configured.', file=sys.stderr)
        return 1
    summary = asyncio.run(_run(settings.database_url, options))
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def _parse_args(settings: Settings) -> CleanupOptions:
    parser = argparse.ArgumentParser(
        description=(
            'Archive expired soft-deleted vault rows (vault_files and their '
            'knowledge_chunks) into the archive tables, move their canonical '
            'files from vault storage into the archive root, and prune aged '
            'vault_file_versions rows. Each path\'s latest version is never '
            'pruned, regardless of age.'
        )
    )
    parser.add_argument('--vault-root', type=Path, default=settings.vault_storage_root)
    parser.add_argument(
        '--archive-root',
        type=Path,
        default=settings.vault_archive_root,
    )
    parser.add_argument(
        '--retention-days',
        type=int,
        default=settings.sync_soft_delete_retention_days,
    )
    parser.add_argument(
        '--version-retention-days',
        type=int,
        default=settings.sync_version_retention_days,
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Report what would change without writing to the database or disk.',
    )
    args = parser.parse_args()
    return CleanupOptions(
        vault_root=cast(Path, args.vault_root).expanduser().resolve(),
        archive_root=cast(Path, args.archive_root).expanduser().resolve(),
        retention_days=cast(int, args.retention_days),
        version_retention_days=cast(int, args.version_retention_days),
        dry_run=cast(bool, args.dry_run),
    )


async def _run(database_url: str, options: CleanupOptions) -> dict[str, object]:
    engine = build_async_engine(database_url)
    sessionmaker = build_sessionmaker(engine)
    try:
        async with sessionmaker() as session:
            archive_summary = await _archive_expired_files(session, options)
            prune_summary = await _prune_versions(session, options)
    finally:
        await engine.dispose()
    return {
        'dry_run': options.dry_run,
        'archive': archive_summary,
        'version_pruning': prune_summary,
    }


def _serialize(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _vault_file_raw_record(row: VaultFile) -> dict[str, Any]:
    return {
        'id': row.id,
        'vault_pk': row.vault_pk,
        'vault_id': row.vault_id,
        'source_path': row.source_path,
        'vectorize': row.vectorize,
        'status': row.status,
        'index_status': row.index_status,
        'index_error': row.index_error,
        'revision': row.revision,
        'deleted': row.deleted,
        'deleted_at': _serialize(row.deleted_at),
        'updated_by_device_id': row.updated_by_device_id,
        'last_synced_at': _serialize(row.last_synced_at),
        'last_indexed_at': _serialize(row.last_indexed_at),
        'created_at': _serialize(row.created_at),
        'updated_at': _serialize(row.updated_at),
    }


def _knowledge_chunk_raw_record(row: KnowledgeChunk) -> dict[str, Any]:
    return {
        'id': row.id,
        'created_at': _serialize(row.created_at),
        'updated_at': _serialize(row.updated_at),
    }


async def _archive_expired_files(
    session: AsyncSession, options: CleanupOptions
) -> dict[str, object]:
    """Archive + move canonical files for vault rows past soft-delete retention.

    For each expired row, the physical file is moved from the vault root to
    the archive root first (matching the prior behavior of this script), then
    the vault_files row and any matching knowledge_chunks rows are copied into
    the archive tables and deleted from the live tables in a single
    transaction (one commit per file). This ordering keeps the operation
    idempotent: if the process is interrupted after the file move but before
    the database commit, a rerun finds the file already moved (reported as
    "missing" at its original location) and still completes the database
    archival for that row -- no file content or database row is ever lost.
    """
    cutoff = func.now() - timedelta(days=options.retention_days)
    result = await session.execute(
        select(VaultFile).where(
            VaultFile.deleted.is_(True),
            VaultFile.deleted_at.is_not(None),
            VaultFile.deleted_at < cutoff,
        )
    )
    expired_rows = list(result.scalars().all())

    archived: list[str] = []
    moved: list[str] = []
    missing: list[str] = []
    for row in expired_rows:
        label = f'{row.vault_id}/{row.source_path}'
        original = safe_vault_destination(
            options.vault_root / row.vault_id, row.source_path
        )
        if original.exists():
            if not options.dry_run:
                destination = safe_vault_destination(
                    options.archive_root / row.vault_id, row.source_path
                )
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(original, destination)
            moved.append(label)
        else:
            missing.append(label)

        if not options.dry_run:
            await _archive_vault_file(session, row)
            await session.commit()
        archived.append(label)

    return {
        'retention_days': options.retention_days,
        'archived': archived,
        'archived_count': len(archived),
        'moved': moved,
        'missing': missing,
        'moved_count': len(moved),
        'missing_count': len(missing),
    }


async def _archive_vault_file(session: AsyncSession, row: VaultFile) -> None:
    chunk_result = await session.execute(
        select(KnowledgeChunk).where(
            KnowledgeChunk.vault_id == row.vault_id,
            KnowledgeChunk.source_path == row.source_path,
        )
    )
    chunks = list(chunk_result.scalars().all())

    session.add(
        ArchivedVaultFile(
            original_id=row.id,
            vault_pk=row.vault_pk,
            vault_id=row.vault_id,
            source_path=row.source_path,
            content_hash=row.content_hash,
            size_bytes=row.size_bytes,
            mime_type=row.mime_type,
            file_type=row.file_type,
            archived_reason=ARCHIVE_REASON_SOFT_DELETE_EXPIRED,
            raw_record=_vault_file_raw_record(row),
        )
    )
    for chunk in chunks:
        session.add(
            ArchivedKnowledgeChunk(
                original_id=chunk.id,
                vault_pk=chunk.vault_pk,
                vault_id=chunk.vault_id,
                source_path=chunk.source_path,
                chunk_index=chunk.chunk_index,
                title=chunk.title,
                heading=chunk.heading,
                heading_path=chunk.heading_path,
                content=chunk.content,
                agent_hint=chunk.agent_hint,
                project=chunk.project,
                domain=chunk.domain,
                type=chunk.type,
                status=chunk.status,
                priority=chunk.priority,
                visibility=chunk.visibility,
                tags=chunk.tags,
                content_hash=chunk.content_hash,
                embedding_model=chunk.embedding_model,
                embedding=chunk.embedding,
                archived_reason=ARCHIVE_REASON_SOFT_DELETE_EXPIRED,
                raw_record=_knowledge_chunk_raw_record(chunk),
            )
        )
        await session.delete(chunk)

    await session.delete(row)


async def _prune_versions(
    session: AsyncSession, options: CleanupOptions
) -> dict[str, object]:
    """Delete aged vault_file_versions rows, always keeping the latest per path.

    A row is eligible for pruning only when both hold: it is older than
    `version_retention_days`, and its revision is strictly less than the
    maximum revision recorded for its (vault_id, source_path) -- so the most
    recent version of every path is never deleted, regardless of age.
    """
    cutoff = func.now() - timedelta(days=options.version_retention_days)
    latest_subquery = (
        select(
            VaultFileVersion.vault_id.label('vault_id'),
            VaultFileVersion.source_path.label('source_path'),
            func.max(VaultFileVersion.revision).label('max_revision'),
        )
        .group_by(VaultFileVersion.vault_id, VaultFileVersion.source_path)
        .subquery()
    )
    candidates_result = await session.execute(
        select(
            VaultFileVersion.id,
            VaultFileVersion.vault_id,
            VaultFileVersion.source_path,
            VaultFileVersion.revision,
        )
        .join(
            latest_subquery,
            and_(
                VaultFileVersion.vault_id == latest_subquery.c.vault_id,
                VaultFileVersion.source_path == latest_subquery.c.source_path,
            ),
        )
        .where(
            VaultFileVersion.revision < latest_subquery.c.max_revision,
            VaultFileVersion.created_at < cutoff,
        )
    )
    candidates = candidates_result.all()
    pruned = [f'{row.vault_id}/{row.source_path}#{row.revision}' for row in candidates]

    if not options.dry_run and candidates:
        ids = [row.id for row in candidates]
        await session.execute(
            delete(VaultFileVersion).where(VaultFileVersion.id.in_(ids))
        )
        await session.commit()

    return {
        'version_retention_days': options.version_retention_days,
        'pruned': pruned,
        'pruned_count': len(pruned),
    }


if __name__ == '__main__':
    raise SystemExit(main())
