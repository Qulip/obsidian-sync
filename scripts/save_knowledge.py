#!/usr/bin/env python3
"""Save an agent-generated knowledge note to a specific Obsidian Sync vault.

Typical two-step usage with list_vaults.py:

  # Step 1: agent lists vaults and picks the best one
  python list_vaults.py --server-url URL --token TOKEN

  # Step 2: agent saves the note to the chosen vault
  python save_knowledge.py --server-url URL --token TOKEN \\
    --vault-id VAULT_ID --title "Note Title" --content "..."

Content options (mutually exclusive):
  --content "inline markdown text"
  --content-file path/to/note.md
  --content-file -                   (read from stdin)

Requires only Python 3 standard library — no uv, pip, or extra packages needed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime

_AGENT_NOTES_FOLDER = 'Agent Notes'


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Save an agent knowledge note to an Obsidian Sync vault.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument('--server-url', required=True, help='Base URL of the sync API')
    parser.add_argument('--token', required=True, help='API bearer token')
    parser.add_argument('--vault-id', required=True, help='Target vault identifier')
    parser.add_argument(
        '--title',
        required=True,
        help='Note title (used for filename when --path is omitted)',
    )

    content_group = parser.add_mutually_exclusive_group(required=True)
    content_group.add_argument('--content', help='Note body as an inline string')
    content_group.add_argument(
        '--content-file',
        metavar='FILE',
        help='Path to a markdown file, or - to read from stdin',
    )

    parser.add_argument(
        '--path', help='Custom path inside the vault (e.g. "Folder/note.md")'
    )
    parser.add_argument('--tags', help='Comma-separated tags added to frontmatter')
    parser.add_argument('--project', help='Source project name added to frontmatter')
    parser.add_argument(
        '--no-reindex', action='store_true', help='Skip reindex request after upload'
    )

    args = parser.parse_args()

    try:
        server_url = _parse_server_url(args.server_url)
        return _save(server_url, args)
    except RuntimeError as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1


def _save(server_url: str, args: argparse.Namespace) -> int:
    body = _read_content(args)
    note_path = _resolve_path(args)
    tags = [t.strip() for t in args.tags.split(',')] if args.tags else []
    project: str | None = args.project

    full_content = _build_markdown(args.title, tags, project, body)
    content_bytes = full_content.encode('utf-8')
    content_hash = hashlib.sha256(content_bytes).hexdigest()
    size = len(content_bytes)

    # Step 1: declare what we intend to upload
    manifest_data = _post(
        server_url,
        args.token,
        f'/vaults/{args.vault_id}/sync/manifest',
        {'files': [{'path': note_path, 'hash': content_hash, 'size': size}]},
    )

    need_upload = manifest_data.get('need_upload')
    if not isinstance(need_upload, list) or note_path not in need_upload:
        _print({'status': 'skipped', 'path': note_path, 'reason': 'unchanged'})
        return 0

    # Step 2: upload file content
    upload_data = _post(
        server_url,
        args.token,
        f'/vaults/{args.vault_id}/sync/files',
        {
            'path': note_path,
            'hash': content_hash,
            'content': full_content,
            'size': size,
            'mime_type': 'text/markdown',
        },
    )

    # Step 3: reindex (non-fatal — Ollama may not be running)
    if not args.no_reindex:
        try:
            _post(
                server_url,
                args.token,
                f'/vaults/{args.vault_id}/reindex',
                {'mode': 'changed_only'},
            )
        except RuntimeError:
            pass

    _print(
        {
            'status': upload_data.get('status', 'uploaded'),
            'vault_id': args.vault_id,
            'path': note_path,
            'hash': content_hash,
        }
    )
    return 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_content(args: argparse.Namespace) -> str:
    if args.content is not None:
        return args.content
    if args.content_file == '-':
        return sys.stdin.read()
    with open(args.content_file, encoding='utf-8') as fh:
        return fh.read()


def _resolve_path(args: argparse.Namespace) -> str:
    if args.path:
        p = args.path.strip()
        return p if p.endswith('.md') else f'{p}.md'
    date_str = datetime.now(UTC).strftime('%Y-%m-%d')
    slug = _slugify(args.title)
    return f'{_AGENT_NOTES_FOLDER}/{date_str}-{slug}.md'


def _build_markdown(title: str, tags: list[str], project: str | None, body: str) -> str:
    date_str = datetime.now(UTC).strftime('%Y-%m-%d')
    lines = ['---', f'title: "{title}"', f'date: {date_str}']
    if tags:
        lines.append(f'tags: [{", ".join(tags)}]')
    lines.append('source: agent')
    if project:
        lines.append(f'project: {project}')
    lines += ['---', '', f'# {title}', '', body.strip(), '']
    return '\n'.join(lines)


def _slugify(title: str) -> str:
    slug = title.lower()
    slug = re.sub(r'[^\w\s-]', '', slug)
    slug = re.sub(r'[\s_-]+', '-', slug)
    slug = slug.strip('-')
    return slug[:60]


def _parse_server_url(raw: str) -> str:
    url = raw.rstrip('/')
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {'http', 'https'}:
        raise RuntimeError('--server-url must use http or https')
    if not parsed.netloc:
        raise RuntimeError('--server-url must include a host')
    return url


def _print(data: dict[str, object]) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------------------
# HTTP primitives (stdlib only)
# ---------------------------------------------------------------------------


def _post(
    server_url: str,
    token: str,
    path: str,
    payload: dict[str, object],
) -> dict[str, object]:
    req = urllib.request.Request(
        f'{server_url}{path}',
        data=json.dumps(payload).encode('utf-8'),
        headers={
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json',
            'User-Agent': 'save-knowledge/0.1',
        },
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as response:  # nosec B310
            body = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode('utf-8', errors='replace')
        raise RuntimeError(f'POST {path} failed (HTTP {exc.code}): {detail}') from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f'POST {path} failed: {exc.reason}') from exc

    decoded = json.loads(body)
    if not isinstance(decoded, dict) or decoded.get('success') is not True:
        raise RuntimeError(f'POST {path} returned error: {decoded.get("error")}')
    data = decoded.get('data')
    if not isinstance(data, dict):
        raise RuntimeError(f'POST {path} returned no data field')
    return data  # type: ignore[return-value]


if __name__ == '__main__':
    raise SystemExit(main())
