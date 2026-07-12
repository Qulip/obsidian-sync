import base64
import os
from pathlib import Path
from typing import Any
from urllib.parse import quote

from obsidian_sync.domain.files import (
    IMAGE_MAX_BYTES,
    PDF_MAX_BYTES,
    base64_encoded_size,
)
from obsidian_sync.domain.hashing import sha256_bytes, sha256_text
from obsidian_sync.sync_agent.client import encode_vault_path

DEVICE = 'dev1'


def _files_url(vault_id: str, path: str) -> str:
    return f'/vaults/{vault_id}/files/{encode_vault_path(path)}'


def _put(
    client: Any,
    headers: dict[str, str],
    vault_id: str,
    path: str,
    *,
    base_revision: int,
    content: str,
    content_hash: str | None = None,
    device_id: str = DEVICE,
) -> Any:
    return client.put(
        _files_url(vault_id, path),
        json={
            'device_id': device_id,
            'base_revision': base_revision,
            'content_hash': content_hash or sha256_text(content),
            'content': content,
        },
        headers=headers,
    )


def _put_binary(
    client: Any,
    headers: dict[str, str],
    vault_id: str,
    path: str,
    *,
    base_revision: int,
    content: bytes,
    content_hash: str | None = None,
    device_id: str = DEVICE,
) -> Any:
    return client.put(
        _files_url(vault_id, path),
        json={
            'device_id': device_id,
            'base_revision': base_revision,
            'content_hash': content_hash or sha256_bytes(content),
            'content': base64.b64encode(content).decode('ascii'),
            'encoding': 'base64',
        },
        headers=headers,
    )


def _delete(
    client: Any,
    headers: dict[str, str],
    vault_id: str,
    path: str,
    *,
    base_revision: int,
) -> Any:
    return client.request(
        'DELETE',
        _files_url(vault_id, path),
        json={'device_id': DEVICE, 'base_revision': base_revision},
        headers=headers,
    )


def _storage_path(vault_id: str, path: str) -> Path:
    root = Path(os.environ['OBSIDIAN_SYNC_VAULT_STORAGE_ROOT'])
    return root / vault_id / path


# --- auth -----------------------------------------------------------------


def test_missing_token_returns_401_envelope(app_client: Any) -> None:
    response = app_client.get('/vaults')
    assert response.status_code == 401
    body = response.json()
    assert body['success'] is False
    assert body['error']['code'] == 'UNAUTHORIZED'


def test_bad_token_returns_401(app_client: Any) -> None:
    response = app_client.get('/vaults', headers={'Authorization': 'Bearer nope'})
    assert response.status_code == 401
    assert response.json()['error']['code'] == 'UNAUTHORIZED'


# --- device registration --------------------------------------------------


def test_device_registration_is_idempotent(
    app_client: Any, auth_headers: dict[str, str], vault_id: str
) -> None:
    url = f'/vaults/{vault_id}/sync/devices'
    body = {'device_id': DEVICE, 'device_name': 'Laptop'}
    first = app_client.post(url, json=body, headers=auth_headers)
    second = app_client.post(url, json=body, headers=auth_headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()['data']['registered'] is True


# --- put / create / update ------------------------------------------------


def test_first_upload_creates_revision_one(
    app_client: Any, auth_headers: dict[str, str], vault_id: str
) -> None:
    response = _put(
        app_client,
        auth_headers,
        vault_id,
        'notes/a.md',
        base_revision=0,
        content='# hello',
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['success'] is True
    data = body['data']
    assert data['revision'] == 1
    assert data['path'] == 'notes/a.md'
    assert data['content_hash'] == sha256_text('# hello')


def test_duplicate_create_conflicts_and_persists_conflict_row(
    app_client: Any,
    auth_headers: dict[str, str],
    vault_id: str,
    db_fetch: Any,
) -> None:
    _put(
        app_client,
        auth_headers,
        vault_id,
        'notes/a.md',
        base_revision=0,
        content='v1',
    )
    response = _put(
        app_client,
        auth_headers,
        vault_id,
        'notes/a.md',
        base_revision=0,
        content='v2',
    )
    assert response.status_code == 409
    error = response.json()['error']
    assert error['code'] == 'SYNC_CONFLICT'
    details = error['details']
    assert details['client_base_revision'] == 0
    assert details['server_revision'] == 1
    assert details['server_content_hash'] == sha256_text('v1')

    rows = db_fetch(
        'SELECT status FROM obsidian.sync_conflicts '
        'WHERE vault_id = $1 AND source_path = $2',
        vault_id,
        'notes/a.md',
    )
    assert len(rows) == 1
    assert rows[0]['status'] == 'OPEN'


def test_update_with_matching_base_revision_increments(
    app_client: Any, auth_headers: dict[str, str], vault_id: str
) -> None:
    _put(
        app_client,
        auth_headers,
        vault_id,
        'notes/a.md',
        base_revision=0,
        content='v1',
    )
    response = _put(
        app_client,
        auth_headers,
        vault_id,
        'notes/a.md',
        base_revision=1,
        content='v2',
    )
    assert response.status_code == 200
    assert response.json()['data']['revision'] == 2


def test_update_with_stale_base_revision_conflicts(
    app_client: Any, auth_headers: dict[str, str], vault_id: str
) -> None:
    _put(
        app_client,
        auth_headers,
        vault_id,
        'notes/a.md',
        base_revision=0,
        content='v1',
    )
    _put(
        app_client,
        auth_headers,
        vault_id,
        'notes/a.md',
        base_revision=1,
        content='v2',
    )
    response = _put(
        app_client,
        auth_headers,
        vault_id,
        'notes/a.md',
        base_revision=1,
        content='v3',
    )
    assert response.status_code == 409
    assert response.json()['error']['code'] == 'SYNC_CONFLICT'


def test_content_hash_mismatch_returns_400(
    app_client: Any, auth_headers: dict[str, str], vault_id: str
) -> None:
    response = _put(
        app_client,
        auth_headers,
        vault_id,
        'notes/a.md',
        base_revision=0,
        content='real',
        content_hash=sha256_text('other'),
    )
    assert response.status_code == 400
    assert response.json()['error']['code'] == 'VALIDATION_ERROR'


def test_traversal_path_is_rejected(
    app_client: Any, auth_headers: dict[str, str], vault_id: str
) -> None:
    url = f'/vaults/{vault_id}/files/' + quote('../escape.md', safe='')
    response = app_client.put(
        url,
        json={
            'device_id': DEVICE,
            'base_revision': 0,
            'content_hash': sha256_text('x'),
            'content': 'x',
        },
        headers=auth_headers,
    )
    assert response.status_code >= 400
    assert response.status_code < 500


def test_non_markdown_path_returns_unsupported_type(
    app_client: Any, auth_headers: dict[str, str], vault_id: str
) -> None:
    response = _put(
        app_client,
        auth_headers,
        vault_id,
        'notes/file.txt',
        base_revision=0,
        content='plain',
    )
    assert response.status_code == 400
    assert response.json()['error']['code'] == 'UNSUPPORTED_FILE_TYPE'


# --- request size limit ---------------------------------------------------


def test_request_body_over_limit_returns_413(
    app_client: Any, auth_headers: dict[str, str], vault_id: str
) -> None:
    # The request-size ceiling covers the largest allowed attachment
    # (a base64-encoded PDF up to PDF_MAX_BYTES) alongside markdown
    # content, so the oversized body must exceed that larger bound -- see
    # `app.create_app`'s `_MAX_ATTACHMENT_REQUEST_BYTES` computation.
    oversized = 'a' * (base64_encoded_size(PDF_MAX_BYTES) + 256 * 1024)
    response = _put(
        app_client,
        auth_headers,
        vault_id,
        'notes/big.md',
        base_revision=0,
        content=oversized,
        content_hash=sha256_text('x'),
    )
    assert response.status_code == 413
    body = response.json()
    assert body['error']['code'] == 'VALIDATION_ERROR'
    assert body['error']['message'] == 'request body too large'


# --- get ------------------------------------------------------------------


def test_get_falls_back_to_version_when_canonical_corrupt(
    app_client: Any, auth_headers: dict[str, str], vault_id: str
) -> None:
    _put(
        app_client,
        auth_headers,
        vault_id,
        'notes/a.md',
        base_revision=0,
        content='body',
    )
    canonical = _storage_path(vault_id, 'notes/a.md')
    canonical.write_text('CORRUPTED ON DISK', encoding='utf-8')

    response = app_client.get(_files_url(vault_id, 'notes/a.md'), headers=auth_headers)
    assert response.status_code == 200
    data = response.json()['data']
    assert data['content'] == 'body'
    assert data['content_hash'] == sha256_text('body')


def test_get_file_returns_content_and_revision(
    app_client: Any, auth_headers: dict[str, str], vault_id: str
) -> None:
    _put(
        app_client,
        auth_headers,
        vault_id,
        'notes/a.md',
        base_revision=0,
        content='body',
    )
    response = app_client.get(_files_url(vault_id, 'notes/a.md'), headers=auth_headers)
    assert response.status_code == 200
    data = response.json()['data']
    assert data['content'] == 'body'
    assert data['revision'] == 1
    assert data['deleted'] is False


def test_get_missing_file_returns_404(
    app_client: Any, auth_headers: dict[str, str], vault_id: str
) -> None:
    response = app_client.get(
        _files_url(vault_id, 'notes/missing.md'), headers=auth_headers
    )
    assert response.status_code == 404
    assert response.json()['error']['code'] == 'NOT_FOUND'


# --- delete / restore -----------------------------------------------------


def test_delete_soft_deletes_and_keeps_history(
    app_client: Any,
    auth_headers: dict[str, str],
    vault_id: str,
    db_fetch: Any,
) -> None:
    _put(
        app_client,
        auth_headers,
        vault_id,
        'notes/a.md',
        base_revision=0,
        content='body',
    )
    canonical = _storage_path(vault_id, 'notes/a.md')
    assert canonical.exists()

    response = _delete(
        app_client, auth_headers, vault_id, 'notes/a.md', base_revision=1
    )
    assert response.status_code == 200
    assert response.json()['data']['deleted'] is True

    rows = db_fetch(
        'SELECT deleted FROM obsidian.vault_files '
        'WHERE vault_id = $1 AND source_path = $2',
        vault_id,
        'notes/a.md',
    )
    assert len(rows) == 1
    assert rows[0]['deleted'] is True

    versions = db_fetch(
        'SELECT revision FROM obsidian.vault_file_versions '
        'WHERE vault_id = $1 AND source_path = $2',
        vault_id,
        'notes/a.md',
    )
    assert len(versions) >= 1
    assert canonical.exists()

    get_response = app_client.get(
        _files_url(vault_id, 'notes/a.md'), headers=auth_headers
    )
    assert get_response.status_code == 404

    changes = app_client.get(
        f'/vaults/{vault_id}/sync/changes?since=0', headers=auth_headers
    ).json()['data']['changes']
    assert changes[-1]['event_type'] == 'DELETE'
    assert changes[-1]['deleted'] is True


def test_delete_with_stale_base_revision_conflicts(
    app_client: Any, auth_headers: dict[str, str], vault_id: str
) -> None:
    _put(
        app_client,
        auth_headers,
        vault_id,
        'notes/a.md',
        base_revision=0,
        content='body',
    )
    response = _delete(
        app_client, auth_headers, vault_id, 'notes/a.md', base_revision=99
    )
    assert response.status_code == 409
    assert response.json()['error']['code'] == 'SYNC_CONFLICT'


def test_recreate_after_soft_delete_emits_create(
    app_client: Any, auth_headers: dict[str, str], vault_id: str
) -> None:
    _put(
        app_client,
        auth_headers,
        vault_id,
        'notes/a.md',
        base_revision=0,
        content='body',
    )
    _delete(app_client, auth_headers, vault_id, 'notes/a.md', base_revision=1)
    response = _put(
        app_client,
        auth_headers,
        vault_id,
        'notes/a.md',
        base_revision=0,
        content='reborn',
    )
    assert response.status_code == 200

    changes = app_client.get(
        f'/vaults/{vault_id}/sync/changes?since=0', headers=auth_headers
    ).json()['data']['changes']
    assert changes[-1]['event_type'] == 'CREATE'
    assert changes[-1]['deleted'] is False


def test_restore_recovers_content_and_emits_restore(
    app_client: Any, auth_headers: dict[str, str], vault_id: str
) -> None:
    _put(
        app_client,
        auth_headers,
        vault_id,
        'notes/a.md',
        base_revision=0,
        content='original',
    )
    _delete(app_client, auth_headers, vault_id, 'notes/a.md', base_revision=1)
    response = app_client.post(
        f'/vaults/{vault_id}/sync/restore',
        json={'path': 'notes/a.md', 'device_id': DEVICE},
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text

    get_response = app_client.get(
        _files_url(vault_id, 'notes/a.md'), headers=auth_headers
    )
    assert get_response.status_code == 200
    assert get_response.json()['data']['content'] == 'original'

    changes = app_client.get(
        f'/vaults/{vault_id}/sync/changes?since=0', headers=auth_headers
    ).json()['data']['changes']
    assert changes[-1]['event_type'] == 'RESTORE'


# --- changes cursor -------------------------------------------------------


def test_changes_cursor_pagination_and_limit(
    app_client: Any, auth_headers: dict[str, str], vault_id: str
) -> None:
    _put(app_client, auth_headers, vault_id, 'a.md', base_revision=0, content='a')
    _put(app_client, auth_headers, vault_id, 'b.md', base_revision=0, content='b')
    _put(app_client, auth_headers, vault_id, 'c.md', base_revision=0, content='c')

    all_changes = app_client.get(
        f'/vaults/{vault_id}/sync/changes?since=0', headers=auth_headers
    ).json()['data']
    assert len(all_changes['changes']) == 3
    to_cursor = all_changes['to_cursor']

    empty = app_client.get(
        f'/vaults/{vault_id}/sync/changes?since={to_cursor}',
        headers=auth_headers,
    ).json()['data']
    assert empty['changes'] == []

    limited = app_client.get(
        f'/vaults/{vault_id}/sync/changes?since=0&limit=2', headers=auth_headers
    ).json()['data']
    assert len(limited['changes']) == 2


# --- status ---------------------------------------------------------------


def test_status_counts_move_as_expected(
    app_client: Any, auth_headers: dict[str, str], vault_id: str
) -> None:
    app_client.post(
        f'/vaults/{vault_id}/sync/devices',
        json={'device_id': DEVICE},
        headers=auth_headers,
    )
    _put(app_client, auth_headers, vault_id, 'a.md', base_revision=0, content='a')
    status = app_client.get(
        f'/vaults/{vault_id}/sync/status?device_id={DEVICE}',
        headers=auth_headers,
    ).json()['data']
    assert status['server_revision'] == 1
    assert status['pending_changes'] == 1
    assert status['open_conflicts'] == 0

    _put(
        app_client,
        auth_headers,
        vault_id,
        'a.md',
        base_revision=0,
        content='dupe',
    )
    status2 = app_client.get(
        f'/vaults/{vault_id}/sync/status', headers=auth_headers
    ).json()['data']
    assert status2['open_conflicts'] == 1


# --- vectorizing flags ----------------------------------------------------


def test_normal_markdown_is_flagged_for_vectorizing(
    app_client: Any,
    auth_headers: dict[str, str],
    vault_id: str,
    db_fetch: Any,
) -> None:
    _put(
        app_client,
        auth_headers,
        vault_id,
        'notes/a.md',
        base_revision=0,
        content='body',
    )
    rows = db_fetch(
        'SELECT vectorize, index_status FROM obsidian.vault_files '
        'WHERE vault_id = $1 AND source_path = $2',
        vault_id,
        'notes/a.md',
    )
    assert rows[0]['vectorize'] is True
    assert rows[0]['index_status'] == 'pending'


def test_conflict_named_file_is_not_vectorized(
    app_client: Any,
    auth_headers: dict[str, str],
    vault_id: str,
    db_fetch: Any,
) -> None:
    path = 'notes/x.conflict.dev.20260707-000000.md'
    response = _put(
        app_client,
        auth_headers,
        vault_id,
        path,
        base_revision=0,
        content='body',
    )
    assert response.status_code == 200, response.text
    rows = db_fetch(
        'SELECT vectorize, index_status FROM obsidian.vault_files '
        'WHERE vault_id = $1 AND source_path = $2',
        vault_id,
        path,
    )
    assert rows[0]['vectorize'] is False
    assert rows[0]['index_status'] == 'skipped'


# --- attachments (P2-5) ----------------------------------------------------


def test_image_attachment_push_pull_roundtrip(
    app_client: Any,
    auth_headers: dict[str, str],
    vault_id: str,
    db_fetch: Any,
) -> None:
    raw = b'\x89PNG\r\n\x1a\nnot-a-real-png-but-bytes-are-bytes'
    response = _put_binary(
        app_client,
        auth_headers,
        vault_id,
        'attachments/photo.png',
        base_revision=0,
        content=raw,
    )
    assert response.status_code == 200, response.text
    data = response.json()['data']
    assert data['revision'] == 1
    assert data['content_hash'] == sha256_bytes(raw)

    get_response = app_client.get(
        _files_url(vault_id, 'attachments/photo.png'), headers=auth_headers
    )
    assert get_response.status_code == 200
    body = get_response.json()['data']
    assert body['encoding'] == 'base64'
    assert base64.b64decode(body['content']) == raw
    assert body['content_hash'] == sha256_bytes(raw)

    on_disk = _storage_path(vault_id, 'attachments/photo.png')
    assert on_disk.read_bytes() == raw

    rows = db_fetch(
        'SELECT vectorize, index_status, file_type, mime_type '
        'FROM obsidian.vault_files WHERE vault_id = $1 AND source_path = $2',
        vault_id,
        'attachments/photo.png',
    )
    assert rows[0]['vectorize'] is False
    assert rows[0]['index_status'] == 'skipped'
    assert rows[0]['file_type'] == 'image'
    assert rows[0]['mime_type'] == 'image/png'

    # Version history is intentionally skipped for attachments (see
    # RevisionSyncService._write_revision) to avoid unbounded storage growth
    # from full-content binary versions.
    version_rows = db_fetch(
        'SELECT COUNT(*) AS n FROM obsidian.vault_file_versions '
        'WHERE vault_id = $1 AND source_path = $2',
        vault_id,
        'attachments/photo.png',
    )
    assert version_rows[0]['n'] == 0


def test_markdown_text_response_still_uses_utf8_encoding(
    app_client: Any, auth_headers: dict[str, str], vault_id: str
) -> None:
    _put(
        app_client,
        auth_headers,
        vault_id,
        'notes/a.md',
        base_revision=0,
        content='body',
    )
    response = app_client.get(_files_url(vault_id, 'notes/a.md'), headers=auth_headers)
    data = response.json()['data']
    assert data['encoding'] == 'utf8'
    assert data['content'] == 'body'


def test_excluded_extension_returns_unsupported_file_type(
    app_client: Any, auth_headers: dict[str, str], vault_id: str
) -> None:
    response = _put_binary(
        app_client,
        auth_headers,
        vault_id,
        'attachments/malware.exe',
        base_revision=0,
        content=b'MZ...',
    )
    assert response.status_code == 400
    assert response.json()['error']['code'] == 'UNSUPPORTED_FILE_TYPE'


def test_attachment_over_size_limit_returns_400(
    app_client: Any, auth_headers: dict[str, str], vault_id: str
) -> None:
    oversized = bytes(IMAGE_MAX_BYTES + 1024)
    response = _put_binary(
        app_client,
        auth_headers,
        vault_id,
        'attachments/big.png',
        base_revision=0,
        content=oversized,
    )
    assert response.status_code == 400
    error = response.json()['error']
    assert error['code'] == 'VALIDATION_ERROR'
    assert error['details']['max_bytes'] == IMAGE_MAX_BYTES


def test_attachment_invalid_base64_returns_400(
    app_client: Any, auth_headers: dict[str, str], vault_id: str
) -> None:
    response = app_client.put(
        _files_url(vault_id, 'attachments/bad.png'),
        json={
            'device_id': DEVICE,
            'base_revision': 0,
            'content_hash': sha256_bytes(b'x'),
            'content': 'not valid base64 !!!',
            'encoding': 'base64',
        },
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert response.json()['error']['code'] == 'VALIDATION_ERROR'


def test_pdf_attachment_is_accepted_up_to_its_own_limit(
    app_client: Any,
    auth_headers: dict[str, str],
    vault_id: str,
    db_fetch: Any,
) -> None:
    raw = b'%PDF-1.4 fake pdf body'
    response = _put_binary(
        app_client,
        auth_headers,
        vault_id,
        'attachments/doc.pdf',
        base_revision=0,
        content=raw,
    )
    assert response.status_code == 200, response.text
    rows = db_fetch(
        'SELECT file_type, mime_type FROM obsidian.vault_files '
        'WHERE vault_id = $1 AND source_path = $2',
        vault_id,
        'attachments/doc.pdf',
    )
    assert rows[0]['file_type'] == 'pdf'
    assert rows[0]['mime_type'] == 'application/pdf'


def test_attachment_delete_and_restore_is_unsupported_by_history(
    app_client: Any, auth_headers: dict[str, str], vault_id: str
) -> None:
    """Attachments have no version history, so restore after delete 404s.

    This documents the accepted v1 trade-off (see
    RevisionSyncService._write_revision's docstring): version storage is
    skipped for attachments to avoid unbounded binary storage growth, at the
    cost of restore support.
    """
    raw = b'\x89PNG\r\n\x1a\nphoto-bytes'
    put_response = _put_binary(
        app_client,
        auth_headers,
        vault_id,
        'attachments/photo.png',
        base_revision=0,
        content=raw,
    )
    revision = put_response.json()['data']['revision']

    delete_response = _delete(
        app_client,
        auth_headers,
        vault_id,
        'attachments/photo.png',
        base_revision=revision,
    )
    assert delete_response.status_code == 200, delete_response.text

    restore_response = app_client.post(
        f'/vaults/{vault_id}/sync/restore',
        json={
            'path': 'attachments/photo.png',
            'device_id': DEVICE,
            'restore_revision': revision,
        },
        headers=auth_headers,
    )
    assert restore_response.status_code == 404
    assert restore_response.json()['error']['code'] == 'NOT_FOUND'


# --- unicode path ---------------------------------------------------------


def test_unicode_path_round_trip(
    app_client: Any, auth_headers: dict[str, str], vault_id: str
) -> None:
    path = '한국어/노트 이름.md'
    put_response = _put(
        app_client,
        auth_headers,
        vault_id,
        path,
        base_revision=0,
        content='한글 본문',
    )
    assert put_response.status_code == 200, put_response.text

    get_response = app_client.get(_files_url(vault_id, path), headers=auth_headers)
    assert get_response.status_code == 200
    data = get_response.json()['data']
    assert data['path'] == path
    assert data['content'] == '한글 본문'


# --- existing endpoints smoke --------------------------------------------


def test_existing_endpoints_still_respond(
    app_client: Any, auth_headers: dict[str, str], vault_id: str
) -> None:
    listing = app_client.get('/vaults', headers=auth_headers)
    assert listing.status_code == 200
    assert any(v['vault_id'] == vault_id for v in listing.json()['data']['vaults'])

    manifest = app_client.post(
        f'/vaults/{vault_id}/sync/manifest',
        json={'files': []},
        headers=auth_headers,
    )
    assert manifest.status_code == 200
    assert manifest.json()['success'] is True
