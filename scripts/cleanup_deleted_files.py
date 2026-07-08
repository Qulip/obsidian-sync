from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import cast

from sqlalchemy import func, select

from obsidian_sync.core.config import Settings
from obsidian_sync.db.models import VaultFile
from obsidian_sync.db.session import build_async_engine, build_sessionmaker
from obsidian_sync.domain.paths import safe_vault_destination


@dataclass(frozen=True, slots=True)
class CleanupOptions:
    vault_root: Path
    archive_root: Path
    retention_days: int


def main() -> int:
    settings = Settings()
    options = _parse_args(settings)
    if settings.database_url is None:
        print('OBSIDIAN_SYNC_DATABASE_URL is not configured.', file=sys.stderr)
        return 1
    summary = asyncio.run(_cleanup(settings.database_url, options))
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def _parse_args(settings: Settings) -> CleanupOptions:
    parser = argparse.ArgumentParser(
        description=(
            'Move canonical files of expired soft-deleted vault rows from the '
            'vault storage root into the archive root. Database rows and '
            'version history are kept intact.'
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
    args = parser.parse_args()
    return CleanupOptions(
        vault_root=cast(Path, args.vault_root).expanduser().resolve(),
        archive_root=cast(Path, args.archive_root).expanduser().resolve(),
        retention_days=cast(int, args.retention_days),
    )


async def _cleanup(database_url: str, options: CleanupOptions) -> dict[str, object]:
    engine = build_async_engine(database_url)
    sessionmaker = build_sessionmaker(engine)
    moved: list[str] = []
    missing: list[str] = []
    try:
        async with sessionmaker() as session:
            result = await session.execute(
                select(VaultFile.vault_id, VaultFile.source_path).where(
                    VaultFile.deleted.is_(True),
                    VaultFile.deleted_at.is_not(None),
                    VaultFile.deleted_at
                    < func.now() - timedelta(days=options.retention_days),
                )
            )
            for vault_id, source_path in result.all():
                label = f'{vault_id}/{source_path}'
                original = safe_vault_destination(
                    options.vault_root / vault_id,
                    source_path,
                )
                if not original.exists():
                    missing.append(label)
                    continue
                destination = safe_vault_destination(
                    options.archive_root / vault_id,
                    source_path,
                )
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(original, destination)
                moved.append(label)
    finally:
        await engine.dispose()
    return {
        'retention_days': options.retention_days,
        'moved': moved,
        'missing': missing,
        'moved_count': len(moved),
        'missing_count': len(missing),
    }


if __name__ == '__main__':
    raise SystemExit(main())
