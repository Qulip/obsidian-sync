from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from obsidian_sync.domain.errors import DomainValidationError
from obsidian_sync.domain.files import FileKind, validate_file_size
from obsidian_sync.domain.hashing import sha256_file

DEFAULT_EXCLUDED_DIRS = ('.obsidian', '.trash')


@dataclass(frozen=True, slots=True)
class LocalFile:
    path: str
    filesystem_path: Path
    content_hash: str
    size: int
    updated_at: datetime
    mime_type: str | None
    kind: FileKind


@dataclass(frozen=True, slots=True)
class UploadOptions:
    vault_id: str
    vault_path: Path
    server_url: str
    token: str
    excluded_dirs: frozenset[str]
    archive_missing: bool
    reindex: bool


def main() -> int:
    options = _parse_args()
    files = list(_scan_vault(options.vault_path, options.excluded_dirs))
    files_by_path = {file.path: file for file in files}

    manifest_data = _post_json(
        options,
        f'/vaults/{options.vault_id}/sync/manifest',
        {'files': [_manifest_entry(file) for file in files]},
    )
    need_upload = _string_list(manifest_data, 'need_upload')
    archived_candidates = _string_list(manifest_data, 'archived_candidates')
    unchanged = _string_list(manifest_data, 'unchanged')

    uploaded = 0
    skipped = 0
    for source_path in need_upload:
        file = files_by_path.get(source_path)
        if file is None:
            raise RuntimeError(f'server requested unknown path: {source_path}')
        if file.kind is not FileKind.MARKDOWN:
            raise RuntimeError(
                'server requested non-Markdown upload unsupported by API: '
                f'{source_path}'
            )
        result = _upload_markdown(options, file)
        if result.get('status') == 'uploaded':
            uploaded += 1
        else:
            skipped += 1

    archived = 0
    if options.archive_missing and archived_candidates:
        archive_data = _post_json(
            options,
            f'/vaults/{options.vault_id}/sync/archive',
            {'paths': archived_candidates, 'reason': 'missing_in_manifest'},
        )
        archived = len(_string_list(archive_data, 'archived'))

    if options.reindex and (uploaded or archived):
        _post_json(
            options,
            f'/vaults/{options.vault_id}/reindex',
            {'mode': 'changed_only'},
        )

    print(
        json.dumps(
            {
                'scanned': len(files),
                'need_upload': len(need_upload),
                'uploaded': uploaded,
                'skipped_uploads': skipped,
                'unchanged': len(unchanged),
                'archived': archived,
                'reindex_requested': options.reindex and (uploaded or archived) > 0,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _parse_args() -> UploadOptions:
    parser = argparse.ArgumentParser(
        description='Upload changed Obsidian Markdown files to the sync API.'
    )
    parser.add_argument('--vault-id', required=True)
    parser.add_argument('--vault-path', required=True, type=Path)
    parser.add_argument('--server-url', required=True)
    parser.add_argument('--token', required=True)
    parser.add_argument(
        '--exclude-dir',
        action='append',
        default=[],
        help='Directory name to skip. May be passed more than once.',
    )
    parser.add_argument(
        '--no-archive',
        action='store_true',
        help='Do not archive server files missing from the local manifest.',
    )
    parser.add_argument(
        '--no-reindex',
        action='store_true',
        help='Do not request changed_only reindexing after upload/archive.',
    )
    args = parser.parse_args()
    excluded_dirs = frozenset(
        (*DEFAULT_EXCLUDED_DIRS, *cast(list[str], args.exclude_dir))
    )
    return UploadOptions(
        vault_id=cast(str, args.vault_id),
        vault_path=cast(Path, args.vault_path).expanduser().resolve(),
        server_url=_server_url(cast(str, args.server_url)),
        token=cast(str, args.token),
        excluded_dirs=excluded_dirs,
        archive_missing=not cast(bool, args.no_archive),
        reindex=not cast(bool, args.no_reindex),
    )


def _scan_vault(vault_path: Path, excluded_dirs: frozenset[str]) -> Iterable[LocalFile]:
    if not vault_path.is_dir():
        raise RuntimeError(f'vault path is not a directory: {vault_path}')

    for filesystem_path in sorted(
        path for path in vault_path.rglob('*') if path.is_file()
    ):
        relative_path = filesystem_path.relative_to(vault_path)
        if any(part in excluded_dirs for part in relative_path.parts):
            continue
        source_path = relative_path.as_posix()
        size = filesystem_path.stat().st_size
        try:
            policy = validate_file_size(source_path, size)
        except DomainValidationError:
            continue
        yield LocalFile(
            path=policy.source_path,
            filesystem_path=filesystem_path,
            content_hash=sha256_file(filesystem_path),
            size=size,
            updated_at=datetime.fromtimestamp(
                filesystem_path.stat().st_mtime,
                tz=UTC,
            ),
            mime_type=mimetypes.guess_type(filesystem_path.name)[0],
            kind=policy.kind,
        )


def _manifest_entry(file: LocalFile) -> dict[str, object]:
    return {
        'path': file.path,
        'hash': file.content_hash,
        'size': file.size,
        'updated_at': file.updated_at.isoformat(),
        'mime_type': file.mime_type,
    }


def _upload_markdown(options: UploadOptions, file: LocalFile) -> dict[str, object]:
    content = file.filesystem_path.read_text(encoding='utf-8')
    return _post_json(
        options,
        f'/vaults/{options.vault_id}/sync/files',
        {
            'path': file.path,
            'hash': file.content_hash,
            'content': content,
            'size': len(content.encode('utf-8')),
            'mime_type': file.mime_type or 'text/markdown',
        },
    )


def _server_url(raw_url: str) -> str:
    server_url = raw_url.rstrip('/')
    parsed = urllib.parse.urlparse(server_url)
    if parsed.scheme not in {'http', 'https'}:
        raise RuntimeError('--server-url must use http or https')
    if not parsed.netloc:
        raise RuntimeError('--server-url must include a host')
    return server_url


def _post_json(
    options: UploadOptions,
    path: str,
    payload: dict[str, object],
) -> dict[str, object]:
    request = urllib.request.Request(
        f'{options.server_url}{path}',
        data=json.dumps(payload).encode('utf-8'),
        headers={
            'Authorization': f'Bearer {options.token}',
            'Content-Type': 'application/json',
            'User-Agent': 'obsidian-sync-upload/0.1',
        },
        method='POST',
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:  # nosec B310
            body = response.read()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode('utf-8', errors='replace')
        raise RuntimeError(f'POST {path} failed with HTTP {exc.code}: {body}') from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f'POST {path} failed: {exc.reason}') from exc

    decoded = json.loads(body)
    if not isinstance(decoded, dict):
        raise RuntimeError(f'POST {path} returned a non-object response')
    if decoded.get('success') is not True:
        raise RuntimeError(f'POST {path} failed: {decoded.get("error")}')
    data = decoded.get('data')
    if not isinstance(data, dict):
        raise RuntimeError(f'POST {path} returned missing data')
    return cast(dict[str, object], data)


def _string_list(data: dict[str, object], key: str) -> list[str]:
    raw_value = data.get(key)
    if not isinstance(raw_value, list) or not all(
        isinstance(item, str) for item in raw_value
    ):
        raise RuntimeError(f'response field must be a list of strings: {key}')
    return cast(list[str], raw_value)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
