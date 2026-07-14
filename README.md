# Obsidian Sync

개인 Obsidian Vault를 FastAPI 서버에 동기화하고, PostgreSQL +
pgvector와 Ollama embedding으로 검색하는 개인 지식 저장소 서버입니다.

동기화는 **Revision 기반 양방향 sync**를 사용합니다. `obsisync` CLI가
여러 PC의 로컬 Vault와 서버 Canonical Vault를 revision으로 동기화합니다. 같은
파일이 두 PC에서 동시에 수정되면 자동 병합 없이 409 Conflict로 처리하고,
로컬에 `.conflict` 파일을 남깁니다. 자세한 내용은
[Client (sync-agent)](#client-sync-agent)와 [docs/sync-api.md](docs/sync-api.md),
[docs/sync-agent.md](docs/sync-agent.md) 참고.

Agent가 노트 한 건을 저장할 때는 MCP `sync_file` tool
(`force_sync_file`, 내부적으로 revision 시스템과 정합)을 사용합니다.
[Agent / MCP Usage](#agent--mcp-usage) 참고.

일반 클라이언트는 REST API를 사용하고, Agent는 REST MCP API 또는
Streamable HTTP MCP tool을 사용할 수 있습니다.

## Architecture

```text
Obsidian Vault
    |
    | obsisync sync (PUT /vaults/{vault_id}/files/{path})
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

1. `obsisync`가 로컬 변경분을 `base_revision` 기반으로
   `PUT /vaults/{vault_id}/files/{path}`에 올립니다 (또는 Agent가 MCP `sync_file`로
   노트 한 건을 저장합니다).
2. `changed_only` reindex로 Markdown을 chunking하고 embedding을 저장합니다.
3. `/knowledge/search` 또는 MCP tool로 검색합니다.

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

Vault 내용을 로컬 디렉터리와 동기화하려면 [Client (sync-agent)](#client-sync-agent)의
`obsisync sync`를 사용하세요. Revision 기반 PUT/GET/DELETE API의
전체 계약(요청/응답 예시, 충돌 처리, soft delete)은 [docs/sync-api.md](docs/sync-api.md)를
참고하세요.

Storage writes use staged replace and rollback logic. If DB commit fails during upload,
the service attempts to restore the previous file state.

## Client (sync-agent)

`obsisync`는 각 PC에서 실행되는 로컬 클라이언트입니다. 로컬 Vault와
서버 Canonical Vault를 revision 기반으로 양방향 동기화합니다. 여러 PC를
`device_id`로 구분하며, 같은 파일이 동시에 수정되면 자동 병합 없이 로컬에
`.conflict` 파일을 만들어 사용자가 직접 해결하게 합니다.

전체 정책(revision, conflict, soft delete, restore)은 [docs/sync-api.md](docs/sync-api.md),
에이전트 상세 동작은 [docs/sync-agent.md](docs/sync-agent.md)를 참고하세요.

### Prerequisites

클라이언트를 쓰기 전에 서버 쪽에서 아래를 준비합니다.

1. Vault 생성 (`POST /vaults`, [Create Vault](#create-vault) 참고).
2. DB API token 발급 (`POST /tokens`, [API Token](#api-token) 참고). 이 토큰을
   각 PC의 `OBSIDIAN_SYNC_AGENT_TOKEN`으로 사용합니다.

> **인터넷 노출 시 주의**: 서버는 HTTP(8000)만 바인딩하고 내장 TLS가 없습니다.
> Bearer 토큰이 평문으로 오가므로, 외부에서 접속한다면 리버스 프록시(Caddy/nginx)로
> HTTPS를 종단하거나 Tailscale/WireGuard 같은 사설 네트워크 안에서만 접근하세요.

### Install

`obsisync`는 Go로 포팅된 로컬 클라이언트 바이너리를 기본 배포
형태로 사용합니다. FastAPI 서버는 계속 Python 애플리케이션이며, Dockerfile도
서버 실행만 대상으로 합니다.

릴리스에 현재 OS/CPU용 바이너리가 첨부되어 있다면 내려받아 실행 권한을 주고
`PATH`에 둡니다. 이 문서는 아직 게시되지 않은 릴리스 산출물이 있다고 주장하지
않습니다. 릴리스 산출물이 없으면 아래처럼 로컬에서 빌드합니다.

```bash
make build-agent
install dist/obsisync/obsisync /usr/local/bin/obsisync
obsisync --help
```

타깃별 로컬 빌드 산출물이 필요하면 `make build-agent-all`을 실행합니다.
산출물은 git에서 무시되는 `dist/obsisync/` 아래에 생성됩니다.

기존 Python 콘솔 스크립트도 아직 유지됩니다. Go 바이너리가 배포/운영 경로가
되지만, Python CLI는 호환성 확인과 롤백을 위해 명시적인 제거 작업 전까지
남겨둡니다.

```bash
uv sync
uv run obsidian-sync-agent --help
```

### Configuration

설정 우선순위는 **CLI 인자 > 환경변수 > 설정 파일 > 기본값**입니다.
설정 파일 위치는 `{vault_root}/.obsidian-sync-agent/config.json`입니다.

| Setting | CLI | Env | Config file key | Default |
| --- | --- | --- | --- | --- |
| Server base URL | `--server` | `OBSIDIAN_SYNC_AGENT_SERVER` | `server_base_url` | (required) |
| Vault ID | `--vault-id` | `OBSIDIAN_SYNC_AGENT_VAULT_ID` | `vault_id` | (required) |
| Vault root | `--vault-root` | `OBSIDIAN_SYNC_AGENT_VAULT_ROOT` | - | 현재 디렉터리 |
| Device ID | `--device-id` | `OBSIDIAN_SYNC_AGENT_DEVICE_ID` | `device_id` | hostname |
| Device name | - | `OBSIDIAN_SYNC_AGENT_DEVICE_NAME` | `device_name` | - |
| API token | - | `OBSIDIAN_SYNC_AGENT_TOKEN` | - (파일/로그 미저장) | - |
| Require Obsidian refresh | `--require-obsidian-refresh` | - | `require_obsidian_refresh` | `false` |

API token은 보안상 환경변수로만 받습니다. 설정 파일이나 로그에는 저장되지 않습니다.

환경변수만으로 실행하는 예시:

```bash
export OBSIDIAN_SYNC_AGENT_SERVER='http://localhost:8000'
export OBSIDIAN_SYNC_AGENT_VAULT_ID='personal-main'
export OBSIDIAN_SYNC_AGENT_TOKEN='osk_...'

obsisync sync --vault-root "$HOME/ObsidianVault"
```

설정 파일로 관리하는 예시 (`~/ObsidianVault/.obsidian-sync-agent/config.json`):

```json
{
  "server_base_url": "http://localhost:8000",
  "vault_id": "personal-main",
  "device_id": "macbook-pro",
  "device_name": "MacBook Pro",
  "obsidian": {
    "enabled": false,
    "base_url": "https://127.0.0.1:27124",
    "verify_tls": false,
    "reload_command": false
  }
}
```

### Run

한 번의 sync 사이클(pull → 로컬 반영 → scan → push)을 실행합니다.

```bash
obsisync sync --vault-root "$HOME/ObsidianVault"
```

실제 쓰기/전송 없이 계획만 출력:

```bash
obsisync sync --vault-root "$HOME/ObsidianVault" --dry-run
```

서버/로컬 동기화 상태 확인:

```bash
obsisync status --vault-root "$HOME/ObsidianVault"
```

설치된 CLI를 최신 GitHub Release로 갱신하려면:

```bash
obsisync update
```

업데이트 가능 여부를 확인한 뒤 설치 확인을 받습니다.

주기 실행은 cron/launchd/systemd timer로 위 `sync` 명령을 반복 호출하면 됩니다.

Exit codes:

| Code | 의미 |
| --- | --- |
| `0` | sync 성공 (또는 `--dry-run`) |
| `1` | sync는 완료됐지만 conflict 발생 → `.conflict` 파일 확인 필요 |
| `2` | 설정 오류 또는 sync 실패 (서버 연결 불가 등) |
| `3` | `--require-obsidian-refresh` 지정 시 Obsidian refresh 단계 실패 |

### Local State

에이전트는 Vault 안 숨김 디렉터리에 상태를 보관합니다. 이 디렉터리는 동기화와
벡터화 대상에서 제외됩니다.

- `{vault_root}/.obsidian-sync-agent/manifest.json`: 마지막 sync cursor와 파일별
  revision/hash. 손상 시 삭제하면 다음 sync에서 재구성됩니다.
- `{vault_root}/.obsidian-sync-agent/config.json`: 선택적 설정 파일.

무시되는 항목: `.obsidian/`, `.obsidian-sync-agent/`, `.trash/`, `.DS_Store`,
`Thumbs.db`, `*.conflict.*.md`, `*.sync-conflict*.md`, 그 밖의 숨김 파일.
v1에서는 `.md` 파일만 동기화합니다.

### Conflict Resolution

같은 파일이 다른 PC에서 먼저 수정돼 서버 revision이 앞서 있으면, push가 409로
거부되고 로컬에 아래 이름의 파일이 생성됩니다.

```text
Notes/JPA.conflict.macbook-pro.20260707-121500.md
```

이 파일에는 로컬 버전과 서버 버전이 모두 담깁니다. 해결 방법:

1. `.conflict` 파일을 열어 두 버전을 비교하고 원본(`Notes/JPA.md`)을 원하는
   내용으로 직접 정리합니다.
2. `.conflict` 파일을 삭제합니다.
3. 다시 `obsisync sync`를 실행합니다.

다음 sync는 conflict 당시 서버 revision을 기준으로 정리된 원본 파일을
업로드합니다. 원본 파일을 바꾸지 않은 상태에서는 unresolved conflict로 보고
다시 push하지 않습니다.

`.conflict` 파일은 동기화·벡터화 대상에서 제외되므로 서버로 올라가거나 검색에
포함되지 않습니다.

### Obsidian Local REST API (optional)

Obsidian이 실행 중이면 로컬 디스크 변경을 앱이 file watcher로 자동 인식하므로
별도 연동 없이도 동작합니다. 연동을 켜면(`obsidian.enabled: true`) 에이전트가
Local REST API에 연결 확인을 시도하고, `reload_command: true`일 때만 앱 reload를
실행합니다(저장 안 된 편집 내용이 유실될 수 있어 기본 off). Local REST API 키는
`OBSIDIAN_LOCAL_REST_API_KEY` 환경변수로 전달합니다. 연동 실패는 기본적으로
warning이며 sync 성공에 영향을 주지 않습니다. 자세한 내용은
[docs/sync-agent.md](docs/sync-agent.md)를 참고하세요.

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
- `sync_file_mcp_vaults__vault_id__sync_file_post`
- `reindex_vault_mcp_vaults__vault_id__reindex_post`
- `search_knowledge_mcp_knowledge_search_post`
- `submit_search_feedback_mcp_knowledge_search_feedback_post`
- `get_note_mcp_vaults_note_post`
- `get_sync_status_mcp_vaults__vault_id__sync_status_get`

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
| `POST` | `/vaults/{vault_id}/sync/devices` | Register a sync device |
| `GET` | `/vaults/{vault_id}/sync/changes` | Pull changes since a cursor |
| `GET` | `/vaults/{vault_id}/sync/status` | Server/device sync status |
| `GET` | `/vaults/{vault_id}/files/{path}` | Download a file (revision-based) |
| `PUT` | `/vaults/{vault_id}/files/{path}` | Upload a file with `base_revision` (409 on conflict) |
| `DELETE` | `/vaults/{vault_id}/files/{path}` | Soft delete a file (JSON body) |
| `POST` | `/vaults/{vault_id}/sync/restore` | Restore a soft-deleted file |
| `POST` | `/vaults/{vault_id}/reindex` | Reindex vault |
| `POST` | `/vaults/{vault_id}/reindex/file` | Reindex one file |
| `POST` | `/knowledge/search` | Semantic search |
| `GET` | `/search-logs` | Search log list |
| `GET` | `/mcp/vaults` | Agent-facing vault list |
| `POST` | `/mcp/vaults/{vault_id}/sync/file` | Agent-facing direct Markdown save |
| `POST` | `/mcp/vaults/{vault_id}/reindex` | Agent-facing reindex |
| `POST` | `/mcp/knowledge/search` | Agent-facing search |
| `POST` | `/mcp/knowledge/search/feedback` | Agent-facing search feedback |
| `POST` | `/mcp/vaults/note` | Agent-facing note lookup |
| `GET` | `/mcp/vaults/{vault_id}/sync/status` | Agent-facing sync status |

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
uv run pytest
go test ./...
go vet ./...
make build-agent
make build-agent-all
```

For narrower work, run the smallest relevant pytest target first, then the full
suite before handoff when DB/API behavior changed:

```bash
uv run pytest tests/sync_agent
uv run pytest tests/test_sync_api.py
```

The full pytest suite expects local PostgreSQL access. Pure sync-agent and
domain tests do not require PostgreSQL and can be run independently.

`make build-agent-all` cross-builds the Go sync agent for darwin/arm64,
darwin/amd64, linux/amd64, linux/arm64, and windows/amd64. The Python
server development commands above remain the source of truth for running the
API service locally.

## E2E Smoke Test Outline

Manual smoke flow:

1. Start server with `.env`.
2. `GET /health` with admin token.
3. Verify admin token is rejected by `GET /vaults`.
4. Create DB API token with `POST /tokens`.
5. Create a temporary vault.
6. Upload a Markdown note through `PUT /vaults/{vault_id}/files/{path}` with `base_revision=0`.
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
