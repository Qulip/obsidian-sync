# Obsidian Sync

개인 Obsidian Vault를 FastAPI 서버에 단방향 동기화하고, PostgreSQL +
pgvector와 Ollama embedding으로 검색하는 개인 지식 저장소 서버입니다.

일반 클라이언트는 REST API를 사용하고, Agent는 REST MCP API 또는
Streamable HTTP MCP tool을 사용할 수 있습니다.

## Architecture

```text
Obsidian Vault
    |
    | scripts/upload_obsidian.py
    v
FastAPI obsidian-sync
    |
    +-- vaults/                  원본 Markdown 사본
    +-- archives/                archive 처리된 파일
    +-- PostgreSQL + pgvector
    |   +-- vaults
    |   +-- vault_files
    |   +-- knowledge_chunks
    |   +-- api_tokens
    |   +-- search_logs
    |   +-- index_failure_logs
    |
    +-- Ollama /api/embed        bge-m3 embedding
```

기본 흐름은 다음과 같습니다.

1. 로컬 Vault 파일 목록과 SHA-256 hash를 manifest로 전송합니다.
2. 서버가 변경된 path만 `need_upload`로 반환합니다.
3. 변경된 Markdown 파일을 업로드합니다.
4. `changed_only` reindex로 Markdown을 chunking하고 embedding을 저장합니다.
5. `/knowledge/search` 또는 MCP tool로 검색합니다.

## Requirements

- Python 3.14+
- `uv`
- PostgreSQL 15+
- pgvector extension
- Ollama
- Ollama embedding model: `bge-m3`

```bash
ollama pull bge-m3
```

## Configuration

`.env`를 사용할 수 있습니다. 현재 설정 클래스는 아래 alias를 지원합니다.

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `OBSIDIAN_SYNC_DATABASE_URL` | one of DB URL aliases | - | PostgreSQL URL |
| `OBSIDIAN_POSTGRESQL_URL` | one of DB URL aliases | - | DB URL alias |
| `DATABASE_URL` | one of DB URL aliases | - | DB URL alias |
| `OBSIDIAN_SYNC_API_TOKEN` | one of admin token aliases | - | admin token |
| `KNOWLEDGE_API_TOKEN` | one of admin token aliases | - | admin token alias |
| `OBSIDIAN_SYNC_OLLAMA_BASE_URL` | no | `http://localhost:11434` | Ollama URL |
| `OBSIDIAN_SYNC_EMBEDDING_MODEL` | no | `bge-m3` | Embedding model |
| `OBSIDIAN_SYNC_EMBEDDING_DIMENSION` | no | `1024` | Vector dimension |
| `OBSIDIAN_SYNC_VAULT_STORAGE_ROOT` | no | `vaults` | Stored vault root |
| `OBSIDIAN_SYNC_VAULT_ARCHIVE_ROOT` | no | `archives` | Archive root |

Example:

```bash
OBSIDIAN_SYNC_DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/obsidian
OBSIDIAN_SYNC_API_TOKEN=change-me-admin-token
OBSIDIAN_SYNC_OLLAMA_BASE_URL=http://localhost:11434
```

`postgresql://...` URL은 앱 시작 시 `postgresql+asyncpg://...`로 자동 변환됩니다.

## Database

마이그레이션은 Alembic으로 적용합니다.

```bash
uv run alembic upgrade head
uv run alembic current
```

초기 migration은 `vector` extension을 생성합니다. 최신 migration에는
`knowledge_chunks.embedding`용 HNSW cosine partial index가 포함되어 있습니다.

## Running

```bash
uv sync --dev
uv run uvicorn obsidian_sync.app:app --host 0.0.0.0 --port 8000
```

Swagger UI:

```text
http://localhost:8000/docs
```

CLI entry point:

```bash
uv run obsidian-sync
```

Docker:

```bash
docker build -t obsidian-sync-api .
docker run --rm -p 8000:8000 \
  -e OBSIDIAN_SYNC_DATABASE_URL='postgresql+asyncpg://user:pass@host:5432/db' \
  -e OBSIDIAN_SYNC_API_TOKEN='change-me-admin-token' \
  obsidian-sync-api
```

## Authentication

이 서버에는 두 종류의 token이 있습니다.

### Admin Token

환경변수 `OBSIDIAN_SYNC_API_TOKEN` 또는 `KNOWLEDGE_API_TOKEN`으로 설정합니다.

Admin token이 허용되는 endpoint:

- `GET /health`
- `POST /tokens`
- `GET /tokens`
- `DELETE /tokens/{token_id}`

Admin token은 일반 API와 REST MCP API에서는 거부됩니다.

### API Token

DB에 hash로 저장되는 client token입니다. 일반 API, REST MCP API, MCP tool
호출에 사용합니다.

```bash
curl -sS -X POST http://localhost:8000/tokens \
  -H "Authorization: Bearer $OBSIDIAN_SYNC_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"local-uploader"}'
```

응답의 `data.token` 값을 이후 요청에 사용합니다.

```bash
export API_TOKEN='osk_...'
```

Token 폐기:

```bash
curl -sS -X DELETE http://localhost:8000/tokens/1 \
  -H "Authorization: Bearer $OBSIDIAN_SYNC_API_TOKEN"
```

## Health

```bash
curl -sS http://localhost:8000/health \
  -H "Authorization: Bearer $OBSIDIAN_SYNC_API_TOKEN"
```

`/health`는 DB 연결 상태를 검사하지 않습니다. DB 장애 확인보다는 process/config와
Ollama model 상태를 보는 운영용 endpoint입니다.

응답 예시:

```json
{
  "success": true,
  "data": {
    "status": "ok",
    "app_version": "0.1.0",
    "environment": "local",
    "database_configured": true,
    "storage_root": "vaults",
    "archive_root": "archives",
    "embedding_model": "bge-m3",
    "ollama": {
      "status": "ok",
      "base_url": "http://localhost:11434",
      "model": "bge-m3"
    }
  },
  "error": null
}
```

`ollama.status`는 `ok`, `unreachable`, `model_missing` 중 하나입니다.
Ollama가 정상이 아니면 top-level `status`는 `degraded`입니다.

## Vault Sync

### Create Vault

```bash
curl -sS -X POST http://localhost:8000/vaults \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "vault_id": "personal-main",
    "name": "Personal Main",
    "description": "Personal knowledge base",
    "default_visibility": "personal"
  }'
```

`vault_id`는 소문자, 숫자, hyphen만 허용합니다.

### Upload With Script

```bash
uv run python scripts/upload_obsidian.py \
  --vault-id personal-main \
  --vault-path "$HOME/ObsidianVault" \
  --server-url http://localhost:8000 \
  --token "$API_TOKEN"
```

기본 제외 디렉터리:

- `.obsidian`
- `.trash`

추가 제외:

```bash
uv run python scripts/upload_obsidian.py \
  --vault-id personal-main \
  --vault-path "$HOME/ObsidianVault" \
  --server-url http://localhost:8000 \
  --token "$API_TOKEN" \
  --exclude-dir Archive \
  --exclude-dir Templates
```

주요 옵션:

| Option | Description |
| --- | --- |
| `--vault-id` | Target vault ID |
| `--vault-path` | Local Obsidian vault path |
| `--server-url` | FastAPI base URL |
| `--token` | DB API token |
| `--exclude-dir` | Directory name to skip, repeatable |
| `--no-archive` | Do not archive files missing from local manifest |
| `--no-reindex` | Do not request reindex after upload |

### Manual Sync API

Manifest:

```bash
HASH=$(printf '%s' "$CONTENT" | shasum -a 256 | awk '{print $1}')
SIZE=$(printf '%s' "$CONTENT" | wc -c | tr -d ' ')
CONTENT_JSON=$(printf '%s' "$CONTENT" | uv run python -c 'import json, sys; print(json.dumps(sys.stdin.read()))')

curl -sS -X POST http://localhost:8000/vaults/personal-main/sync/manifest \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{
    \"files\": [
      {
        \"path\": \"Notes/example.md\",
        \"hash\": \"$HASH\",
        \"size\": $SIZE,
        \"mime_type\": \"text/markdown\"
      }
    ]
  }"
```

Upload changed Markdown:

```bash
curl -sS -X POST http://localhost:8000/vaults/personal-main/sync/files \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{
    \"path\": \"Notes/example.md\",
    \"hash\": \"$HASH\",
    \"content\": $CONTENT_JSON,
    \"size\": $SIZE,
    \"mime_type\": \"text/markdown\"
  }"
```

Archive files missing from local manifest:

```bash
curl -sS -X POST http://localhost:8000/vaults/personal-main/sync/archive \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"paths":["Old/note.md"],"reason":"missing_in_manifest"}'
```

Storage writes use staged replace and rollback logic. If DB commit fails during upload,
the service attempts to restore the previous file state.

## Indexing

Reindex changed files:

```bash
curl -sS -X POST http://localhost:8000/vaults/personal-main/reindex \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"mode":"changed_only"}'
```

Full reindex:

```bash
curl -sS -X POST http://localhost:8000/vaults/personal-main/reindex \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"mode":"full"}'
```

Single file reindex:

```bash
curl -sS -X POST http://localhost:8000/vaults/personal-main/reindex/file \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"path":"Notes/example.md"}'
```

Indexing reads Markdown frontmatter, splits content into chunks, creates embeddings
with Ollama, and stores rows in `knowledge_chunks`.

## Search

```bash
curl -sS -X POST http://localhost:8000/knowledge/search \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "vault_id": "personal-main",
    "query": "FastAPI dependency injection pattern",
    "top_k": 5,
    "project": "obsidian-sync",
    "domain": "backend",
    "filters": {
      "types": ["reference", "rule"],
      "status": ["current"],
      "priority": ["high"],
      "visibility": ["personal", "company"],
      "tags": ["fastapi"]
    }
  }'
```

Search defaults:

- `top_k`: 5
- max `top_k`: 10
- default status filter: `current`

Results include:

- `source_path`
- `title`
- `heading_path`
- `content`
- `agent_hint`
- `project`, `domain`, `type`, `priority`, `visibility`, `tags`

Search logs are stored automatically and can be read with:

```bash
curl -sS 'http://localhost:8000/search-logs?limit=20' \
  -H "Authorization: Bearer $API_TOKEN"
```

## Agent / MCP Usage

The app exposes two Agent-facing surfaces.

### REST MCP API

These are normal HTTP endpoints under `/mcp/*`, protected by DB API token.

```bash
curl -sS http://localhost:8000/mcp/vaults \
  -H "Authorization: Bearer $API_TOKEN"
```

Agent direct note save:

```bash
curl -sS -X POST http://localhost:8000/mcp/vaults/personal-main/sync/file \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "path": "Agent/example.md",
    "content": "---\ntitle: Agent Example\ntype: reference\nproject: obsidian-sync\ndomain: backend\nstatus: current\npriority: medium\nvisibility: personal\ntags: [agent]\nvectorize: true\ncreated: 2026-07-01\nupdated: 2026-07-01\n---\n# Agent Example\n\nSaved by an agent.\n",
    "mime_type": "text/markdown"
  }'
```

MCP search:

```bash
curl -sS -X POST http://localhost:8000/mcp/knowledge/search \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"vault_id":"personal-main","query":"deployment checklist","top_k":5}'
```

### Streamable HTTP MCP

The MCP server uses Streamable HTTP at:

```text
POST /mcp
```

Available tools:

- `list_vaults_mcp_vaults_get`
- `sync_manifest_mcp_vaults__vault_id__sync_manifest_post`
- `sync_file_mcp_vaults__vault_id__sync_file_post`
- `reindex_vault_mcp_vaults__vault_id__reindex_post`
- `search_knowledge_mcp_knowledge_search_post`

Pass the DB API token as an `Authorization: Bearer ...` header in the MCP client.
Tool calls fail with 401 unless a valid DB API token is forwarded.

Admin token is intentionally rejected for MCP tool calls.

Recommended Agent workflow:

1. `list_vaults`
2. `search_knowledge`
3. Use `source_path`, `heading_path`, `content`, and `agent_hint`
4. Save new notes with `sync_file`
5. Run `reindex_vault` with `mode=changed_only`

## Markdown Frontmatter

Vectorized Markdown must start with frontmatter:

```yaml
---
title: Example Note
type: reference
project: obsidian-sync
domain: backend
status: current
priority: medium
visibility: personal
tags: [fastapi, python]
vectorize: true
created: 2026-07-01
updated: 2026-07-01
---
```

Required fields:

- `title`
- `type`
- `project`
- `domain`
- `status`
- `priority`
- `visibility`
- `tags`
- `vectorize`
- `created`
- `updated`

Allowed values:

| Field | Values |
| --- | --- |
| `type` | `reference`, `rule`, `decision`, `issue-solution`, `study-note`, `prompt`, `command`, `checklist` |
| `status` | `current`, `draft`, `deprecated`, `archived` |
| `priority` | `high`, `medium`, `low` |
| `visibility` | `personal`, `company`, `confidential`, `public` |
| `vectorize` | `true`, `false` |
| `created`, `updated` | `YYYY-MM-DD` |

Files with missing or invalid frontmatter fail indexing and are recorded in
`index_failure_logs`.

## File Policy

Allowed extensions:

- `.md`
- `.png`
- `.jpg`
- `.jpeg`
- `.gif`
- `.webp`
- `.pdf`

Only Markdown files are currently uploadable through the JSON file upload endpoints
and only Markdown is vectorized.

Size limits:

| Kind | Limit |
| --- | --- |
| Markdown | 2 MB |
| Image | 10 MB |
| PDF | 30 MB |

Explicitly excluded extensions include `.env`, `.key`, `.pem`, `.p12`, `.zip`,
`.7z`, `.mp4`, `.mov`, `.exe`, `.dll`, `.db`, and `.sqlite`.

Source paths must be relative POSIX paths. Absolute paths, backslashes, empty parts,
`.` and `..` traversal are rejected.

## API Summary

Admin token:

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/health` | Process/config/Ollama status |
| `POST` | `/tokens` | Create DB API token |
| `GET` | `/tokens` | List DB API tokens |
| `DELETE` | `/tokens/{token_id}` | Revoke DB API token |

DB API token:

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/vaults` | Create vault |
| `GET` | `/vaults` | List vaults |
| `POST` | `/vaults/{vault_id}/sync/manifest` | Detect changed files |
| `POST` | `/vaults/{vault_id}/sync/files` | Upload Markdown with hash/size check |
| `POST` | `/vaults/{vault_id}/sync/archive` | Archive removed files |
| `POST` | `/vaults/{vault_id}/reindex` | Reindex vault |
| `POST` | `/vaults/{vault_id}/reindex/file` | Reindex one file |
| `POST` | `/knowledge/search` | Semantic search |
| `GET` | `/search-logs` | Search log list |
| `GET` | `/mcp/vaults` | Agent-facing vault list |
| `POST` | `/mcp/vaults/{vault_id}/sync/manifest` | Agent-facing manifest |
| `POST` | `/mcp/vaults/{vault_id}/sync/file` | Agent-facing direct Markdown save |
| `POST` | `/mcp/vaults/{vault_id}/reindex` | Agent-facing reindex |
| `POST` | `/mcp/knowledge/search` | Agent-facing search |

## Backup

Vault and archive directories can be backed up with:

```bash
uv run python scripts/backup_vaults.py \
  --vault-root vaults \
  --archive-root archives \
  --backup-dir backups
```

Specific vault:

```bash
uv run python scripts/backup_vaults.py \
  --vault-root vaults \
  --archive-root archives \
  --backup-dir backups \
  --vault-id personal-main
```

PostgreSQL backup is separate. Use `pg_dump` for database backup.

## Development

Useful commands:

```bash
uv sync --dev
uv run ruff check .
uv run ruff format .
uv run mypy
uv run python -m unittest discover -v
```

Focused checks used for the current implementation:

```bash
uv run ruff check \
  src/obsidian_sync/core/auth.py \
  src/obsidian_sync/api/router.py \
  src/obsidian_sync/api/routes/health.py \
  src/obsidian_sync/repositories/tokens.py \
  src/obsidian_sync/services/storage.py \
  src/obsidian_sync/services/vault_sync.py \
  alembic/versions/20260701_0003_add_chunks_embedding_hnsw.py \
  tests

uv run mypy \
  src/obsidian_sync/core/auth.py \
  src/obsidian_sync/api/router.py \
  src/obsidian_sync/api/routes/health.py \
  src/obsidian_sync/repositories/tokens.py \
  src/obsidian_sync/services/storage.py \
  src/obsidian_sync/services/vault_sync.py \
  tests

uv run python -m unittest discover -v
```

Known current caveat: repository-wide `ruff check .` and `mypy` may include
unrelated script/type issues outside the core API path. Prefer checking changed
files when working on a narrow patch, then clean up global issues separately.

## E2E Smoke Test Outline

Manual smoke flow:

1. Start server with `.env`.
2. `GET /health` with admin token.
3. Verify admin token is rejected by `GET /vaults`.
4. Create DB API token with `POST /tokens`.
5. Create a temporary vault.
6. Upload a Markdown note through `/vaults/{vault_id}/sync/*`.
7. Upload another note through `/mcp/vaults/{vault_id}/sync/file`.
8. Run `/vaults/{vault_id}/reindex` with `changed_only`.
9. Search with `/knowledge/search`.
10. Use an MCP client against `/mcp` and call `list_vaults`.
11. Revoke the temporary API token.

Example server command:

```bash
set -a
source .env
set +a
uv run uvicorn obsidian_sync.app:app --host 127.0.0.1 --port 8000
```
