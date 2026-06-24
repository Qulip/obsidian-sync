from __future__ import annotations

import argparse
import io
import json
import tarfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast


@dataclass(frozen=True, slots=True)
class BackupOptions:
    vault_root: Path
    archive_root: Path
    backup_dir: Path
    output: Path | None
    vault_id: str | None


def main() -> int:
    options = _parse_args()
    backup_path = _backup_path(options)
    backup_path.parent.mkdir(parents=True, exist_ok=True)

    roots = _backup_roots(options)
    with tarfile.open(backup_path, mode='w:gz') as archive:
        for source, arcname in roots:
            if source.exists():
                archive.add(source, arcname=arcname)
        _add_manifest(archive, backup_path, roots)

    print(
        json.dumps(
            {
                'backup_path': str(backup_path),
                'included': [arcname for source, arcname in roots if source.exists()],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _parse_args() -> BackupOptions:
    parser = argparse.ArgumentParser(
        description='Create a compressed tar backup of synced vault storage.'
    )
    parser.add_argument('--vault-root', type=Path, default=Path('vaults'))
    parser.add_argument('--archive-root', type=Path, default=Path('archives'))
    parser.add_argument('--backup-dir', type=Path, default=Path('backups'))
    parser.add_argument('--output', type=Path)
    parser.add_argument('--vault-id', help='Back up only one vault id.')
    args = parser.parse_args()
    return BackupOptions(
        vault_root=cast(Path, args.vault_root).expanduser().resolve(),
        archive_root=cast(Path, args.archive_root).expanduser().resolve(),
        backup_dir=cast(Path, args.backup_dir).expanduser().resolve(),
        output=cast(Path | None, args.output),
        vault_id=cast(str | None, args.vault_id),
    )


def _backup_path(options: BackupOptions) -> Path:
    if options.output is not None:
        return options.output.expanduser().resolve()
    timestamp = datetime.now(tz=UTC).strftime('%Y%m%dT%H%M%SZ')
    suffix = f'-{options.vault_id}' if options.vault_id else ''
    return options.backup_dir / f'obsidian-sync{suffix}-{timestamp}.tar.gz'


def _backup_roots(options: BackupOptions) -> list[tuple[Path, str]]:
    if options.vault_id:
        return [
            (options.vault_root / options.vault_id, f'vaults/{options.vault_id}'),
            (
                options.archive_root / options.vault_id,
                f'archives/{options.vault_id}',
            ),
        ]
    return [
        (options.vault_root, 'vaults'),
        (options.archive_root, 'archives'),
    ]


def _add_manifest(
    archive: tarfile.TarFile,
    backup_path: Path,
    roots: list[tuple[Path, str]],
) -> None:
    payload = json.dumps(
        {
            'created_at': datetime.now(tz=UTC).isoformat(),
            'backup_path': str(backup_path),
            'roots': [
                {'source': str(source), 'archive_name': arcname}
                for source, arcname in roots
                if source.exists()
            ],
        },
        indent=2,
        sort_keys=True,
    ).encode('utf-8')
    info = tarfile.TarInfo('backup-manifest.json')
    info.size = len(payload)
    info.mtime = int(datetime.now(tz=UTC).timestamp())
    archive.addfile(info, fileobj=io.BytesIO(payload))


if __name__ == '__main__':
    raise SystemExit(main())
