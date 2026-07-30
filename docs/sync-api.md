# Sync API

Revision-based sync API입니다. 각 Vault는 단조 증가하는 `current_revision` 카운터를 가지며, 모든 파일 변경이 이 카운터에서 고유한 revision 번호를 발급받습니다.

## Architecture

```
obsisync (로컬 vault)
        |
        |  Bearer token  (HTTP)
        v
  Sync API  (/vaults/{vault_id}/...)
        |
        +-- vault storage  (vaults/<vault_id>/<path>)  canonical 파일
        |       |
        |       | PUT/RESTORE commit 성공 → index_status='pending'
        |       | post_sync_indexing_enabled=True면 in-process best-effort indexing 예약
        v       v
  PostgreSQL  (vault_files, sync_events, sync_conflicts, vault_file_versions)
        |
        | POST /vaults/{vault_id}/reindex (full rebuild / retry / restart recovery)
        v
  ReindexService  →  Ollama bge-m3 embed  →  knowledge_chunks (pgvector)
        |
        | /mcp, /knowledge/search
        v
  Agent / MCP client
```

---

## 인증

모든 sync 엔드포인트는 DB API token을 사용합니다. Admin token은 `/tokens` 관리 엔드포인트 전용이며 sync 및 MCP 엔드포인트에서는 거부됩니다.

```bash
# Admin token으로 DB API token 발급
curl -sS -X POST http://localhost:8000/tokens \
  -H "Authorization: Bearer $OBSIDIAN_SYNC_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"my-device"}'
# 응답의 data.token 값을 이후 요청에 사용합니다.
export API_TOKEN='osk_...'
```

Token은 서버 로그에 기록되지 않습니다.

---

## 응답 Envelope

모든 엔드포인트는 공통 envelope를 반환합니다.

성공:

```json
{
  "success": true,
  "data": { ... },
  "error": null
}
```

오류:

```json
{
  "success": false,
  "data": null,
  "error": {
    "code": "ERROR_CODE",
    "message": "Human-readable message.",
    "details": { ... }
  }
}
```

### 오류 코드

| Code | HTTP | 설명 |
|---|---|---|
| `UNAUTHORIZED` | 401 | Bearer token 누락 또는 유효하지 않음 |
| `FORBIDDEN` | 403 | 접근 권한 없음 |
| `VALIDATION_ERROR` | 400 | 요청 파라미터 오류 |
| `NOT_FOUND` | 404 | Vault 또는 파일이 존재하지 않음 |
| `CONFLICT_DETECTED` | 409 | DB unique constraint 충돌 |
| `SYNC_CONFLICT` | 409 | Revision 불일치 (낙관적 잠금 실패) |
| `UNSUPPORTED_FILE_TYPE` | 400 | `.md` 이외 파일 |
| `INDEXING_FAILED` | 500 | Vectorizing 처리 오류 |
| `EMBEDDING_FAILED` | 502 | Ollama 임베딩 오류 |
| `INTERNAL_ERROR` | 500 | 서버 내부 오류 |

---

## Revision 정책

- `vaults.current_revision`: Vault 전역 단조 증가 카운터
- PUT/DELETE/RESTORE 성공마다 카운터를 1 증가시켜 새로운 revision 번호를 발급합니다
- `vault_files.revision`: 해당 파일의 마지막 이벤트 revision
- `base_revision`은 **파일의 revision** (`vault_files.revision`)과 비교됩니다 — Vault의 현재 커서가 아닙니다
- 새 파일 생성 또는 soft-delete된 경로 재생성: `base_revision=0`
- `content_hash`: 64자리 lowercase hex SHA-256 (prefix 없음)

---

## API 목록

### 디바이스 등록

```
POST /vaults/{vault_id}/sync/devices
```

디바이스를 등록하거나 갱신합니다. 동일한 `device_id`로 재호출하면 upsert됩니다.

**요청 본문:**

```json
{
  "device_id": "my-laptop",
  "device_name": "My Laptop"
}
```

**응답:**

```json
{
  "success": true,
  "data": {
    "vault_id": "personal-main",
    "device_id": "my-laptop",
    "registered": true
  },
  "error": null
}
```

**curl 예시:**

```bash
curl -sS -X POST "http://localhost:8000/vaults/personal-main/sync/devices" \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"device_id":"my-laptop","device_name":"My Laptop"}'
```

---

### 변경 이벤트 조회

```
GET /vaults/{vault_id}/sync/changes
```

지정한 revision cursor 이후의 sync 이벤트를 반환합니다. `device_id`를 함께 전달하면 해당 디바이스의 `last_seen_revision`이 갱신됩니다.

**Query 파라미터:**

| 파라미터 | 기본값 | 설명 |
|---|---|---|
| `since` | `0` | 이 revision 이후의 이벤트를 반환 (ge=0) |
| `device_id` | - | (선택) 디바이스 cursor 갱신 |
| `limit` | `500` | 최대 1000 |

**응답:**

```json
{
  "success": true,
  "data": {
    "vault_id": "personal-main",
    "from_cursor": 10,
    "to_cursor": 13,
    "changes": [
      {
        "revision": 11,
        "path": "Notes/example.md",
        "event_type": "UPDATE",
        "content_hash": "a3f1c2d4e5b6...",
        "deleted": false,
        "created_at": "2026-07-07T12:00:00"
      },
      {
        "revision": 12,
        "path": "Daily/2026-07-07.md",
        "event_type": "CREATE",
        "content_hash": "9b8c7d6e5f4a...",
        "deleted": false,
        "created_at": "2026-07-07T12:01:00"
      },
      {
        "revision": 13,
        "path": "Archive/old.md",
        "event_type": "DELETE",
        "content_hash": "1a2b3c4d5e6f...",
        "deleted": true,
        "created_at": "2026-07-07T12:02:00"
      }
    ]
  },
  "error": null
}
```

`event_type` 값: `CREATE`, `UPDATE`, `DELETE`, `RESTORE`

**curl 예시:**

```bash
curl -sS "http://localhost:8000/vaults/personal-main/sync/changes?since=10&device_id=my-laptop" \
  -H "Authorization: Bearer $API_TOKEN"
```

---

### Sync 상태 조회

```
GET /vaults/{vault_id}/sync/status
```

**Query 파라미터:**

| 파라미터 | 설명 |
|---|---|
| `device_id` | (선택) 디바이스의 last_seen_revision도 포함 |

**응답:**

```json
{
  "success": true,
  "data": {
    "vault_id": "personal-main",
    "device_id": "my-laptop",
    "server_revision": 42,
    "device_last_seen_revision": 38,
    "pending_changes": 4,
    "open_conflicts": 0,
    "pending_vectorizing_jobs": 2
  },
  "error": null
}
```

**curl 예시:**

```bash
curl -sS "http://localhost:8000/vaults/personal-main/sync/status?device_id=my-laptop" \
  -H "Authorization: Bearer $API_TOKEN"
```

---

### 파일 조회

```
GET /vaults/{vault_id}/files/{file_path:path}
```

soft-delete된 파일은 404를 반환합니다.

중첩 경로 및 유니코드 경로는 퍼센트 인코딩이 필요합니다.

디스크의 canonical 파일 내용은 DB에 기록된 `content_hash`와 대조 검증됩니다. 불일치(FS/DB split-brain) 시 서버는 해당 경로의 최신 저장 버전(`vault_file_versions`) 내용으로 폴백하여 항상 hash와 일치하는 내용을 반환합니다. 폴백할 버전이 없으면 500 `INTERNAL_ERROR`를 반환합니다.

**응답:**

```json
{
  "success": true,
  "data": {
    "vault_id": "personal-main",
    "path": "Notes/example.md",
    "revision": 11,
    "content_hash": "a3f1c2d4e5b6...",
    "content": "# Example\n\nHello world.",
    "deleted": false
  },
  "error": null
}
```

**curl 예시 (중첩/유니코드 경로):**

```bash
# Notes/한글 메모.md
ENCODED_PATH=$(python3 -c "from urllib.parse import quote; print('/'.join(quote(s, safe='') for s in 'Notes/한글 메모.md'.split('/')))")
curl -sS "http://localhost:8000/vaults/personal-main/files/${ENCODED_PATH}" \
  -H "Authorization: Bearer $API_TOKEN"
```

---

### 파일 저장 (PUT)

```
PUT /vaults/{vault_id}/files/{file_path:path}
```

Markdown 파일을 생성하거나 업데이트합니다.

- `base_revision=0`: 새 파일 생성. 활성 파일이 이미 존재하면 409 SYNC_CONFLICT
- `base_revision=0` + soft-delete된 경로: 재생성 허용 (CREATE 이벤트)
- `base_revision > 0`: 파일의 `revision`과 정확히 일치해야 합니다
- 동일한 `base_revision`으로 동일한 `content_hash`를 다시 전송하면 idempotent replay (성공, revision 미변경)
- `.md` 이외 파일: 400 UNSUPPORTED_FILE_TYPE
- 요청 본문 크기 제한: `Content-Length`가 `sync_max_content_bytes`(기본 10MB) + 64KB(JSON envelope 여유분)를 초과하면 파싱 전에 413 `VALIDATION_ERROR`(`request body too large`)로 거부됩니다. 파싱 이후의 content 크기 검증도 그대로 유지됩니다.

성공한 revision-backed Markdown 저장이 벡터화 대상이면 `index_status='pending'`으로 표시합니다. `post_sync_indexing_enabled=True`일 때는 같은 프로세스 안에서 best-effort 인덱싱도 예약합니다. 응답은 임베딩 완료를 기다리지 않습니다. durable queue가 아니므로 프로세스 재시작으로 예약 작업이 사라질 수 있고, 멀티 프로세스 전달도 보장하지 않습니다. 그래도 pending 행은 남으므로 이후 명시적인 `/reindex` 호출로 복구합니다.

**요청 본문:**

```json
{
  "device_id": "my-laptop",
  "base_revision": 11,
  "content_hash": "b4c5d6e7f8a9...",
  "content": "# Example\n\nUpdated content."
}
```

**응답:**

```json
{
  "success": true,
  "data": {
    "vault_id": "personal-main",
    "path": "Notes/example.md",
    "revision": 15,
    "content_hash": "b4c5d6e7f8a9..."
  },
  "error": null
}
```

**curl 예시:**

```bash
CONTENT="# Example\n\nUpdated content."
HASH=$(printf '%s' "$CONTENT" | shasum -a 256 | awk '{print $1}')
CONTENT_JSON=$(printf '%s' "$CONTENT" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')

curl -sS -X PUT "http://localhost:8000/vaults/personal-main/files/Notes/example.md" \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"device_id\":\"my-laptop\",\"base_revision\":11,\"content_hash\":\"${HASH}\",\"content\":${CONTENT_JSON}}"
```

---

### 409 SYNC_CONFLICT 응답

`base_revision`이 서버의 파일 revision과 다를 때 반환됩니다. 충돌 정보는 `sync_conflicts` 테이블에 `status='OPEN'`으로 기록됩니다.

```json
{
  "success": false,
  "data": null,
  "error": {
    "code": "SYNC_CONFLICT",
    "message": "Sync conflict detected. Fetch latest changes and retry.",
    "details": {
      "vault_id": "personal-main",
      "path": "Notes/example.md",
      "client_base_revision": 11,
      "server_revision": 14,
      "server_content_hash": "c5d6e7f8a9b0..."
    }
  }
}
```

자동 병합은 수행하지 않습니다. 클라이언트는 최신 변경사항을 가져와서 충돌을 직접 해소한 뒤 재시도해야 합니다. Obsidian의 sync-agent는 이 상황에서 conflict 파일을 디스크에 기록합니다.

---

### 파일 삭제 (Soft Delete)

```
DELETE /vaults/{vault_id}/files/{file_path:path}
```

JSON 본문이 필요합니다 (FastAPI는 DELETE에서 request body를 지원합니다).

**요청 본문:**

```json
{
  "device_id": "my-laptop",
  "base_revision": 15
}
```

**응답:**

```json
{
  "success": true,
  "data": {
    "vault_id": "personal-main",
    "path": "Notes/example.md",
    "revision": 16,
    "deleted": true
  },
  "error": null
}
```

**curl 예시:**

```bash
curl -sS -X DELETE "http://localhost:8000/vaults/personal-main/files/Notes/example.md" \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"device_id":"my-laptop","base_revision":15}'
```

삭제 시 즉시 처리되는 사항:
- `vault_files.deleted=true`, `deleted_at` 설정, revision 증가
- `vault_files.status='archived'`, `index_status='skipped'`
- `knowledge_chunks` 행 즉시 삭제 (벡터 인덱스에서 제거)
- DELETE 이벤트 기록

canonical 파일(`vaults/<vault_id>/...`)은 retention 기간이 지날 때까지 디스크에 유지됩니다.

---

### Soft Delete 보존 기간 및 정리

기본 보존 기간: 7일 (`sync_soft_delete_retention_days` 설정)

만료된 soft-deleted 파일의 canonical 파일을 `archives/`로 이동합니다. DB 행과 버전 기록은 삭제되지 않습니다.

```bash
# 기본 설정으로 실행
uv run python scripts/cleanup_deleted_files.py

# 보존 기간 직접 지정
uv run python scripts/cleanup_deleted_files.py \
  --vault-root vaults \
  --archive-root archives \
  --retention-days 30
```

출력 예시 (JSON):

```json
{
  "retention_days": 7,
  "moved": ["personal-main/Notes/old.md"],
  "missing": [],
  "moved_count": 1,
  "missing_count": 0
}
```

---

### 파일 복구 (Restore)

```
POST /vaults/{vault_id}/sync/restore
```

soft-delete된 파일을 이전 버전에서 복구합니다. `restore_revision`을 지정하면 해당 revision의 버전을 사용하고, 생략하면 가장 최신 저장 버전을 사용합니다. 복구 후 RESTORE 이벤트가 기록되고, 벡터화 대상 Markdown이면 `index_status='pending'`으로 설정합니다. `post_sync_indexing_enabled=True`일 때는 같은 프로세스 안에서 best-effort 인덱싱도 예약합니다. 응답은 임베딩 완료를 기다리지 않으며, 예약 작업이 사라진 경우에는 명시적인 `/reindex` 호출로 복구합니다.

이 엔드포인트가 body-based인 이유: `{file_path:path}` 파라미터 뒤에 literal segment(`/restore`)를 붙이면 FastAPI 라우팅이 불안정합니다.

**요청 본문:**

```json
{
  "path": "Notes/example.md",
  "device_id": "my-laptop",
  "restore_revision": 15
}
```

`restore_revision`을 생략하면 최신 버전에서 복구합니다.

**응답:**

```json
{
  "success": true,
  "data": {
    "vault_id": "personal-main",
    "path": "Notes/example.md",
    "revision": 20,
    "content_hash": "b4c5d6e7f8a9..."
  },
  "error": null
}
```

**curl 예시:**

```bash
curl -sS -X POST "http://localhost:8000/vaults/personal-main/sync/restore" \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"path":"Notes/example.md","device_id":"my-laptop"}'
```

활성 파일에 대해 restore를 호출하면 409 CONFLICT_DETECTED를 반환합니다.

---

## 스키마 요약

revision sync와 관련된 주요 테이블:

| 테이블 | 역할 |
|---|---|
| `vaults` | `current_revision` (Vault 전역 카운터) |
| `vault_files` | 파일별 `revision`, `deleted`, `index_status` |
| `vault_file_versions` | PUT/RESTORE마다 저장되는 버전 기록 |
| `sync_events` | 이벤트 로그 (`revision` 고유) |
| `sync_conflicts` | 충돌 기록 (`OPEN` / `RESOLVED` / `IGNORED`) |
| `sync_devices` | 디바이스별 `last_seen_revision` |
| `knowledge_chunks` | pgvector 청크 (soft delete 시 즉시 삭제) |
