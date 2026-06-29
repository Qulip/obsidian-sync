#!/usr/bin/env python3
"""Print available Obsidian Sync vaults as JSON.

The agent reads this output and picks the most appropriate vault
before calling save_knowledge.py.

Usage:
  python list_vaults.py --server-url URL --token TOKEN

Requires only Python 3 standard library.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request


def main() -> int:
    parser = argparse.ArgumentParser(description='List Obsidian Sync vaults.')
    parser.add_argument('--server-url', required=True, help='Base URL of the sync API')
    parser.add_argument('--token', required=True, help='API bearer token')
    args = parser.parse_args()

    try:
        server_url = _parse_server_url(args.server_url)
        vaults = _get_vaults(server_url, args.token)
        print(json.dumps({'vaults': vaults}, indent=2, ensure_ascii=False))
        return 0
    except RuntimeError as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1


def _get_vaults(server_url: str, token: str) -> list[object]:
    req = urllib.request.Request(
        f'{server_url}/vaults',
        headers={'Authorization': f'Bearer {token}', 'User-Agent': 'list-vaults/0.1'},
        method='GET',
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:  # nosec B310
            body = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode('utf-8', errors='replace')
        raise RuntimeError(f'GET /vaults failed (HTTP {exc.code}): {detail}') from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f'GET /vaults failed: {exc.reason}') from exc

    decoded = json.loads(body)
    if not isinstance(decoded, dict) or decoded.get('success') is not True:
        raise RuntimeError(f'GET /vaults returned error: {decoded.get("error")}')
    data = decoded.get('data')
    if not isinstance(data, dict):
        raise RuntimeError('GET /vaults returned no data field')
    vaults = data.get('vaults', [])
    if not isinstance(vaults, list):
        raise RuntimeError('GET /vaults returned unexpected vaults shape')
    return vaults


def _parse_server_url(raw: str) -> str:
    url = raw.rstrip('/')
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {'http', 'https'}:
        raise RuntimeError('--server-url must use http or https')
    if not parsed.netloc:
        raise RuntimeError('--server-url must include a host')
    return url


if __name__ == '__main__':
    raise SystemExit(main())
