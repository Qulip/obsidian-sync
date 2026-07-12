"""Tests for P1: MCP `sync_file` overwrite policy + auditability hardening.

Covers the behaviors added on top of the pre-existing `test_mcp_vault_sync.py`
coverage:

- `McpSyncFileRequest.base_revision`: opt-in strict optimistic-concurrency
  writes that reuse `RevisionSyncService.put_file` (same conflict detection /
  `sync_conflicts` recording / idempotent-replay semantics as the
  bidirectional sync API).
- `base_revision` + `overwrite=True` being mutually exclusive (400).
- `api_tokens.allow_overwrite` gating forced overwrites (403 regardless of
  whether the target path already exists).
- `sync_events.origin` bookkeeping: 'mcp' for ordinary MCP writes,
  'mcp_overwrite' for forced replacements, and NULL (unchanged) for writes
  through the bidirectional revision sync API.
- The INFO audit log emitted when a forced overwrite replaces existing
  content.
"""

import asyncio
import hashlib
import logging
import os
from typing import Any

import asyncpg
import pytest

from obsidian_sync.domain.hashing import sha256_text
from obsidian_sync.sync_agent.client import encode_vault_path

TEST_ASYNC_URL = os.environ['OBSIDIAN_SYNC_DATABASE_URL']
TEST_DSN = TEST_ASYNC_URL.replace('postgresql+asyncpg://', 'postgresql://', 1)

MCP_SYNC_FILE_URL = '/mcp/vaults/{vault_id}/sync/file'
_VAULT_SYNC_LOGGER = 'obsidian_sync.services.vault_sync'


def _sync_file(
    client: Any,
    headers: dict[str, str],
    vault_id: str,
    path: str,
    content: str,
    *,
    base_revision: int | None = None,
    overwrite: bool | None = None,
) -> Any:
    body: dict[str, Any] = {'path': path, 'content': content}
    if base_revision is not None:
        body['base_revision'] = base_revision
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


def _sync_events(db_fetch: Any, vault_id: str, path: str) -> list[dict[str, Any]]:
    rows = db_fetch(
        'SELECT event_type, origin FROM obsidian.sync_events '
        'WHERE vault_id = $1 AND source_path = $2 ORDER BY revision',
        vault_id,
        path,
    )
    return [dict(row) for row in rows]


def _open_conflicts(db_fetch: Any, vault_id: str, path: str) -> list[dict[str, Any]]:
    rows = db_fetch(
        'SELECT status FROM obsidian.sync_conflicts '
        'WHERE vault_id = $1 AND source_path = $2',
        vault_id,
        path,
    )
    return [dict(row) for row in rows]


def _insert_token(raw: str, *, allow_overwrite: bool) -> None:
    async def run() -> None:
        conn = await asyncpg.connect(dsn=TEST_DSN)
        try:
            await conn.execute(
                'INSERT INTO obsidian.api_tokens '
                '(name, token_hash, allow_overwrite) VALUES ($1, $2, $3)',
                f'test-token-{raw}',
                hashlib.sha256(raw.encode('utf-8')).hexdigest(),
                allow_overwrite,
            )
        finally:
            await conn.close()

    asyncio.run(run())


@pytest.fixture
def restricted_auth_headers(clean_db: None) -> dict[str, str]:
    """A token without overwrite permission (`allow_overwrite=false`)."""
    raw = 'restricted-bearer-token'
    _insert_token(raw, allow_overwrite=False)
    return {'Authorization': f'Bearer {raw}'}


@pytest.fixture(autouse=True)
def _guard_against_alembic_disabled_logger_state() -> None:
    """Work around a test-order-dependent footgun, not a P1 bug.

    `alembic/env.py` calls `logging.config.fileConfig(config.config_file_name)`
    with the stdlib default `disable_existing_loggers=True`. If any earlier
    test in the same pytest session imports `obsidian_sync.services.vault_sync`
    (e.g. `tests/test_health.py`, which builds an app via `create_app()`
    without going through the `clean_db`/`_database` fixture chain) *before*
    the session-scoped `_database` fixture runs its first alembic migration,
    that `fileConfig` call permanently sets `.disabled = True` on the
    already-created `obsidian_sync.services.vault_sync` logger. Once that
    happens, `caplog.at_level(..., logger=_VAULT_SYNC_LOGGER)` cannot recover
    it -- pytest's `_force_enable_logging` only patches the global
    `logging.disable()` threshold, not a logger's own `.disabled` flag (see
    `_pytest/logging.py::LogCaptureFixture._force_enable_logging`), so the
    audit-log assertions below silently see zero records depending on
    full-suite test order. This fixture makes this module's audit-log
    coverage deterministic regardless of what ran before it.
    """
    logging.getLogger(_VAULT_SYNC_LOGGER).disabled = False


# --- base_revision: optimistic-concurrency writes via the revision system -


def test_base_revision_zero_creates_new_file_with_mcp_origin(
    app_client: Any,
    auth_headers: dict[str, str],
    vault_id: str,
    db_fetch: Any,
) -> None:
    response = _sync_file(
        app_client, auth_headers, vault_id, 'notes/a.md', '# hello', base_revision=0
    )

    assert response.status_code == 200, response.text
    data = response.json()['data']
    assert data['status'] == 'uploaded'
    assert data['hash'] == sha256_text('# hello')

    row = _vault_file_row(db_fetch, vault_id, 'notes/a.md')
    assert row['revision'] == 1
    assert row['content_hash'] == sha256_text('# hello')

    events = _sync_events(db_fetch, vault_id, 'notes/a.md')
    assert [e['event_type'] for e in events] == ['CREATE']
    assert events[0]['origin'] == 'mcp'


def test_base_revision_matching_current_updates_and_bumps_revision(
    app_client: Any,
    auth_headers: dict[str, str],
    vault_id: str,
    db_fetch: Any,
) -> None:
    _sync_file(
        app_client, auth_headers, vault_id, 'notes/a.md', '# v1', base_revision=0
    )

    response = _sync_file(
        app_client, auth_headers, vault_id, 'notes/a.md', '# v2', base_revision=1
    )

    assert response.status_code == 200, response.text
    data = response.json()['data']
    assert data['status'] == 'uploaded'
    assert data['hash'] == sha256_text('# v2')

    row = _vault_file_row(db_fetch, vault_id, 'notes/a.md')
    assert row['revision'] == 2
    assert row['content_hash'] == sha256_text('# v2')

    events = _sync_events(db_fetch, vault_id, 'notes/a.md')
    assert [e['event_type'] for e in events] == ['CREATE', 'UPDATE']
    assert all(e['origin'] == 'mcp' for e in events)


def test_stale_base_revision_returns_409_and_records_open_conflict(
    app_client: Any,
    auth_headers: dict[str, str],
    vault_id: str,
    db_fetch: Any,
) -> None:
    _sync_file(
        app_client, auth_headers, vault_id, 'notes/a.md', '# v1', base_revision=0
    )
    _sync_file(
        app_client, auth_headers, vault_id, 'notes/a.md', '# v2', base_revision=1
    )

    # Server revision is now 2; base_revision=1 is stale.
    response = _sync_file(
        app_client, auth_headers, vault_id, 'notes/a.md', '# v3', base_revision=1
    )

    assert response.status_code == 409
    error = response.json()['error']
    assert error['code'] == 'SYNC_CONFLICT'
    assert error['details']['client_base_revision'] == 1
    assert error['details']['server_revision'] == 2

    # The rejected write must not have touched stored revision/content.
    row = _vault_file_row(db_fetch, vault_id, 'notes/a.md')
    assert row['revision'] == 2
    assert row['content_hash'] == sha256_text('# v2')

    conflicts = _open_conflicts(db_fetch, vault_id, 'notes/a.md')
    assert len(conflicts) == 1
    assert conflicts[0]['status'] == 'OPEN'


def test_base_revision_idempotent_replay_is_skipped_without_new_revision(
    app_client: Any,
    auth_headers: dict[str, str],
    vault_id: str,
    db_fetch: Any,
) -> None:
    _sync_file(
        app_client, auth_headers, vault_id, 'notes/a.md', '# same', base_revision=0
    )

    response = _sync_file(
        app_client, auth_headers, vault_id, 'notes/a.md', '# same', base_revision=1
    )

    assert response.status_code == 200, response.text
    assert response.json()['data']['status'] == 'skipped'

    row = _vault_file_row(db_fetch, vault_id, 'notes/a.md')
    assert row['revision'] == 1
    assert _sync_events(db_fetch, vault_id, 'notes/a.md') == [
        {'event_type': 'CREATE', 'origin': 'mcp'}
    ]


# --- base_revision / overwrite mutual exclusivity --------------------------


def test_base_revision_with_overwrite_returns_400(
    app_client: Any,
    auth_headers: dict[str, str],
    vault_id: str,
) -> None:
    response = _sync_file(
        app_client,
        auth_headers,
        vault_id,
        'notes/a.md',
        '# v1',
        base_revision=0,
        overwrite=True,
    )

    assert response.status_code == 400
    assert response.json()['error']['code'] == 'VALIDATION_ERROR'


# --- allow_overwrite token permission ---------------------------------------


def test_overwrite_without_token_permission_returns_403_even_for_new_file(
    app_client: Any,
    restricted_auth_headers: dict[str, str],
    vault_id: str,
    db_fetch: Any,
) -> None:
    """403 must trigger on the flag alone, independent of file existence."""
    response = _sync_file(
        app_client,
        restricted_auth_headers,
        vault_id,
        'notes/new.md',
        '# hi',
        overwrite=True,
    )

    assert response.status_code == 403
    assert response.json()['error']['code'] == 'FORBIDDEN'

    rows = db_fetch(
        'SELECT 1 FROM obsidian.vault_files WHERE vault_id = $1 AND source_path = $2',
        vault_id,
        'notes/new.md',
    )
    assert rows == []


def test_overwrite_with_token_permission_succeeds_with_mcp_overwrite_origin(
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
    assert response.json()['data']['status'] == 'uploaded'

    events = _sync_events(db_fetch, vault_id, 'notes/a.md')
    assert [e['event_type'] for e in events] == ['CREATE', 'UPDATE']
    assert events[0]['origin'] == 'mcp'
    assert events[1]['origin'] == 'mcp_overwrite'


# --- audit logging -----------------------------------------------------------


def test_overwrite_emits_info_audit_log_with_new_revision_and_hash(
    app_client: Any,
    auth_headers: dict[str, str],
    vault_id: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Pins the `vault_id`/`path`/`new_revision`/`new_content_hash` fields.

    See `test_overwrite_audit_log_previous_fields_reflect_pre_overwrite_state`
    (below) for the `previous_revision`/`previous_content_hash` fields.
    """
    _sync_file(app_client, auth_headers, vault_id, 'notes/a.md', '# v1')

    with caplog.at_level(logging.INFO, logger=_VAULT_SYNC_LOGGER):
        response = _sync_file(
            app_client, auth_headers, vault_id, 'notes/a.md', '# v2', overwrite=True
        )

    assert response.status_code == 200, response.text

    audit_records = [
        record for record in caplog.records if 'mcp overwrite' in record.getMessage()
    ]
    assert len(audit_records) == 1
    record = audit_records[0]
    assert record.levelno == logging.INFO
    message = record.getMessage()
    assert f'vault_id={vault_id}' in message
    assert 'path=notes/a.md' in message
    assert 'new_revision=2' in message
    assert f'new_content_hash={sha256_text("# v2")}' in message


def test_overwrite_audit_log_previous_fields_reflect_pre_overwrite_state(
    app_client: Any,
    auth_headers: dict[str, str],
    vault_id: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _sync_file(app_client, auth_headers, vault_id, 'notes/a.md', '# v1')

    with caplog.at_level(logging.INFO, logger=_VAULT_SYNC_LOGGER):
        _sync_file(
            app_client, auth_headers, vault_id, 'notes/a.md', '# v2', overwrite=True
        )

    message = next(
        record.getMessage()
        for record in caplog.records
        if 'mcp overwrite' in record.getMessage()
    )
    assert 'previous_revision=1' in message
    assert f'previous_content_hash={sha256_text("# v1")}' in message


# --- regression: bidirectional revision sync API keeps origin NULL ---------


def test_revision_sync_api_put_file_leaves_origin_null(
    app_client: Any,
    auth_headers: dict[str, str],
    vault_id: str,
    db_fetch: Any,
) -> None:
    response = app_client.put(
        f'/vaults/{vault_id}/files/{encode_vault_path("notes/api.md")}',
        json={
            'device_id': 'dev1',
            'base_revision': 0,
            'content_hash': sha256_text('body'),
            'content': 'body',
        },
        headers=auth_headers,
    )

    assert response.status_code == 200, response.text

    events = _sync_events(db_fetch, vault_id, 'notes/api.md')
    assert [e['event_type'] for e in events] == ['CREATE']
    assert events[0]['origin'] is None
