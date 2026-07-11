"""Tests for the MCP one-way sync tool's revision-system integration.

`force_sync_file` (exposed as the `sync_file` MCP tool / REST endpoint under
`/mcp/vaults/{vault_id}/sync/file`) used to overwrite `vault_files`
unconditionally, bypassing the revision counter and `sync_events` log that
the bidirectional (`base_revision`) sync API relies on. These tests pin down
the fixed behavior: writes here must be fail-closed by default and, once a
write happens, must stay in lockstep with the revision system.
"""

from typing import Any

from obsidian_sync.domain.hashing import sha256_text

MCP_SYNC_FILE_URL = '/mcp/vaults/{vault_id}/sync/file'
CHANGES_URL = '/vaults/{vault_id}/sync/changes'


def _sync_file(
    client: Any,
    headers: dict[str, str],
    vault_id: str,
    path: str,
    content: str,
    *,
    overwrite: bool | None = None,
) -> Any:
    body: dict[str, Any] = {'path': path, 'content': content}
    if overwrite is not None:
        body['overwrite'] = overwrite
    return client.post(
        MCP_SYNC_FILE_URL.format(vault_id=vault_id),
        json=body,
        headers=headers,
    )


def _vault_file_row(db_fetch: Any, vault_id: str, path: str) -> dict[str, Any]:
    rows = db_fetch(
        'SELECT revision, content_hash, deleted FROM obsidian.vault_files '
        'WHERE vault_id = $1 AND source_path = $2',
        vault_id,
        path,
    )
    assert len(rows) == 1
    return dict(rows[0])


def _sync_event_types(db_fetch: Any, vault_id: str, path: str) -> list[str]:
    rows = db_fetch(
        'SELECT event_type FROM obsidian.sync_events '
        'WHERE vault_id = $1 AND source_path = $2 ORDER BY revision',
        vault_id,
        path,
    )
    return [row['event_type'] for row in rows]


# --- new file: unchanged behavior + revision-system integration -----------


def test_force_sync_file_creates_new_file_and_bumps_revision(
    app_client: Any,
    auth_headers: dict[str, str],
    vault_id: str,
    db_fetch: Any,
) -> None:
    response = _sync_file(app_client, auth_headers, vault_id, 'notes/a.md', '# hello')

    assert response.status_code == 200, response.text
    data = response.json()['data']
    assert data['status'] == 'uploaded'
    assert data['hash'] == sha256_text('# hello')

    row = _vault_file_row(db_fetch, vault_id, 'notes/a.md')
    assert row['revision'] == 1
    assert row['content_hash'] == sha256_text('# hello')
    assert _sync_event_types(db_fetch, vault_id, 'notes/a.md') == ['CREATE']


# --- fail-closed default: existing file with different content -----------


def test_force_sync_file_without_overwrite_conflicts_on_existing_content(
    app_client: Any,
    auth_headers: dict[str, str],
    vault_id: str,
    db_fetch: Any,
) -> None:
    _sync_file(app_client, auth_headers, vault_id, 'notes/a.md', '# v1')

    response = _sync_file(app_client, auth_headers, vault_id, 'notes/a.md', '# v2')

    assert response.status_code == 409
    error = response.json()['error']
    assert error['code'] == 'CONFLICT_DETECTED'

    # The conflicting write must not have touched the stored revision/content.
    row = _vault_file_row(db_fetch, vault_id, 'notes/a.md')
    assert row['revision'] == 1
    assert row['content_hash'] == sha256_text('# v1')
    assert _sync_event_types(db_fetch, vault_id, 'notes/a.md') == ['CREATE']


def test_force_sync_file_default_overwrite_false_is_explicit(
    app_client: Any,
    auth_headers: dict[str, str],
    vault_id: str,
) -> None:
    _sync_file(app_client, auth_headers, vault_id, 'notes/a.md', '# v1')

    response = _sync_file(
        app_client, auth_headers, vault_id, 'notes/a.md', '# v2', overwrite=False
    )

    assert response.status_code == 409
    assert response.json()['error']['code'] == 'CONFLICT_DETECTED'


# --- opt-in overwrite: revision increments + sync_events recorded --------


def test_force_sync_file_overwrite_true_bumps_revision_and_records_event(
    app_client: Any,
    auth_headers: dict[str, str],
    vault_id: str,
    db_fetch: Any,
) -> None:
    _sync_file(app_client, auth_headers, vault_id, 'notes/a.md', '# v1')

    response = _sync_file(
        app_client, auth_headers, vault_id, 'notes/a.md', '# v2', overwrite=True
    )

    assert response.status_code == 200, response.text
    data = response.json()['data']
    assert data['status'] == 'uploaded'
    assert data['hash'] == sha256_text('# v2')

    row = _vault_file_row(db_fetch, vault_id, 'notes/a.md')
    assert row['revision'] == 2
    assert row['content_hash'] == sha256_text('# v2')
    assert _sync_event_types(db_fetch, vault_id, 'notes/a.md') == ['CREATE', 'UPDATE']


def test_force_sync_file_overwrite_is_visible_to_revision_clients(
    app_client: Any,
    auth_headers: dict[str, str],
    vault_id: str,
) -> None:
    """A base_revision client pulling changes must observe MCP writes."""
    _sync_file(app_client, auth_headers, vault_id, 'notes/a.md', '# v1')
    _sync_file(app_client, auth_headers, vault_id, 'notes/a.md', '# v2', overwrite=True)

    response = app_client.get(
        CHANGES_URL.format(vault_id=vault_id),
        params={'since': 0},
        headers=auth_headers,
    )

    assert response.status_code == 200, response.text
    changes = response.json()['data']['changes']
    paths_and_types = [(item['path'], item['event_type']) for item in changes]
    assert paths_and_types == [
        ('notes/a.md', 'CREATE'),
        ('notes/a.md', 'UPDATE'),
    ]


# --- identical content: no-op regardless of overwrite ----------------------


def test_force_sync_file_identical_content_is_skipped_without_new_revision(
    app_client: Any,
    auth_headers: dict[str, str],
    vault_id: str,
    db_fetch: Any,
) -> None:
    _sync_file(app_client, auth_headers, vault_id, 'notes/a.md', '# same')

    response = _sync_file(
        app_client, auth_headers, vault_id, 'notes/a.md', '# same', overwrite=True
    )

    assert response.status_code == 200, response.text
    assert response.json()['data']['status'] == 'skipped'

    row = _vault_file_row(db_fetch, vault_id, 'notes/a.md')
    assert row['revision'] == 1
    assert _sync_event_types(db_fetch, vault_id, 'notes/a.md') == ['CREATE']
