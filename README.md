# Obsidian Sync

여러 PC의 Obsidian Vault를 자기 서버에 동기화하고, AI 에이전트가 그 노트를
의미 검색으로 꺼내 쓸 수 있게 만드는 셀프호스팅 지식 저장소입니다.

- **여러 PC 동기화** — `obsisync` CLI가 로컬 Vault와 서버를 revision 기반으로
  양방향 동기화합니다. 같은 파일이 두 PC에서 동시에 수정되면 자동 병합 없이
  충돌 파일을 남겨 직접 해결하게 합니다.
- **의미 검색** — PostgreSQL + pgvector와 Ollama 임베딩(`bge-m3`)으로 노트를
  벡터화해 자연어로 검색합니다.
- **AI 에이전트 연동** — Claude Code, Gemini CLI 등이 MCP로 노트를 저장하고
  검색합니다. 에이전트의 장기 기억이 됩니다.

---

## 구성 요소

| 구성 요소 | 역할 | 문서 |
| --- | --- | --- |
| **서버** | FastAPI + PostgreSQL. 노트를 보관하고 벡터화·검색을 담당 | 아래 [서버 운영](#서버-운영) |
| **`obsisync` CLI** | 각 PC에서 로컬 Vault를 서버와 동기화 | [docs/CLI.md](docs/CLI.md) |
| **MCP / 스킬** | AI 에이전트가 노트를 저장·검색 | [docs/AGENT.md](docs/AGENT.md) |

서버는 직접 운영해야 합니다. 개인 서버나 홈랩, 사설 네트워크 안에서 돌리는
것을 전제로 설계되었습니다.

---

## 빠른 시작

### 1. 서버 실행

필요한 것: Python 3.14+, [uv](https://docs.astral.sh/uv/), PostgreSQL 15+
(pgvector 확장), [Ollama](https://ollama.com/).

```bash
ollama pull bge-m3
```

`.env` 파일을 만듭니다.

```bash
OBSIDIAN_SYNC_DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/obsidian
OBSIDIAN_SYNC_API_TOKEN=change-me-admin-token
```

스키마를 적용하고 서버를 띄웁니다.

```bash
uv sync
uv run alembic upgrade head
uv run uvicorn obsidian_sync.app:app --host 0.0.0.0 --port 8000
```

Swagger UI: `http://localhost:8000/docs`

Docker로 실행할 수도 있습니다.

```bash
docker build -t obsidian-sync-api .
docker run --rm -p 8000:8000 \
  -e OBSIDIAN_SYNC_DATABASE_URL='postgresql+asyncpg://user:pass@host:5432/db' \
  -e OBSIDIAN_SYNC_API_TOKEN='change-me-admin-token' \
  obsidian-sync-api
```

### 2. Vault와 토큰 준비

`.env`의 admin token으로 **DB API token**을 발급합니다. 이후 모든 일상 작업은
이 토큰을 씁니다.

```bash
export ADMIN_TOKEN='change-me-admin-token'

curl -sS -X POST http://localhost:8000/tokens \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"my-laptop"}'
```

응답의 `data.token`(`osk_...`)을 보관합니다.

```bash
export API_TOKEN='osk_...'
```

Vault를 만듭니다. `vault_id`는 소문자·숫자·하이픈만 허용합니다.

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

### 3. CLI 설치 및 첫 동기화

동기화할 PC마다 실행합니다.

```bash
# macOS / Linux
bash <(curl -fsSL https://raw.githubusercontent.com/Qulip/obsidian-sync/main/install.sh)
```

```powershell
# Windows
irm https://raw.githubusercontent.com/Qulip/obsidian-sync/main/install.ps1 | iex
```

설정 후 동기화합니다.

```bash
export OBSIDIAN_SYNC_AGENT_SERVER='http://localhost:8000'
export OBSIDIAN_SYNC_AGENT_VAULT_ID='personal-main'
export OBSIDIAN_SYNC_AGENT_TOKEN='osk_...'

# 무엇이 올라갈지 먼저 확인
obsisync sync --vault-root "$HOME/ObsidianVault" --dry-run

# 실제 동기화
obsisync sync --vault-root "$HOME/ObsidianVault"
```

기본적으로 `.md` 파일만 동기화합니다. 설정 파일 사용법, 첨부파일 동기화,
충돌 해결은 [docs/CLI.md](docs/CLI.md)를 참고하세요.

### 4. AI 에이전트 연동 (선택)

MCP 엔드포인트 `<서버주소>/mcp`를 에이전트에 등록하고, `Authorization: Bearer
<DB API token>` 헤더를 보내게 설정합니다.

```bash
claude mcp add --transport http obsidian-sync http://localhost:8000/mcp \
  --header "Authorization: Bearer $API_TOKEN"
```

설치 스크립트에서 knowledge-management 스킬을 함께 설치하면 에이전트가 노트
저장 규칙까지 알고 동작합니다. 자세한 내용은 [docs/AGENT.md](docs/AGENT.md).

---

## 일상 사용

**자동 동기화** — 파일 변경을 감시하며 자동으로 동기화합니다.

```bash
obsisync watch --vault-root "$HOME/ObsidianVault"
```

주기 실행을 선호하면 cron / launchd / systemd timer로 `obsisync sync`를 반복
호출하면 됩니다.

**상태 확인**

```bash
obsisync status --vault-root "$HOME/ObsidianVault"
```

**CLI 업데이트**

```bash
obsisync update
```

**충돌이 생겼을 때** — `obsisync sync`가 종료 코드 `1`을 반환하면 Vault 안에
`.conflict` 파일이 생긴 것입니다. 파일을 열어 로컬·서버 버전을 비교하고 원본을
정리한 뒤 `.conflict` 파일을 지우고 다시 sync 하세요. 자세한 절차와 자동 해결
정책(`local-wins` / `remote-wins`)은 [docs/CLI.md](docs/CLI.md#충돌-해결)에
있습니다.

---

## 보안 주의

서버는 HTTP(8000)만 바인딩하며 내장 TLS가 없습니다. Bearer 토큰이 평문으로
오가므로, 외부에서 접속한다면 반드시 다음 중 하나를 적용하세요.

- 리버스 프록시(Caddy / nginx)로 HTTPS 종단
- Tailscale / WireGuard 같은 사설 네트워크 안에서만 접근

토큰은 두 종류이고 용도가 분리되어 있습니다.

| 토큰 | 발급 | 사용처 |
| --- | --- | --- |
| **Admin token** | 환경변수 | `/health`, `/tokens` 관리 전용 |
| **DB API token** | `POST /tokens` | Vault·검색·MCP 등 나머지 전부 |

Admin token은 일반 API와 MCP에서 **의도적으로 거부**됩니다. 토큰 폐기는
`DELETE /tokens/{token_id}`(admin token 필요)로 합니다.

---

## 서버 운영

### 환경변수

| 변수 | 필수 | 기본값 | 설명 |
| --- | --- | --- | --- |
| `OBSIDIAN_SYNC_DATABASE_URL` | DB URL 중 택1 | - | PostgreSQL URL |
| `OBSIDIAN_POSTGRESQL_URL` | DB URL 중 택1 | - | 위 변수의 별칭 |
| `DATABASE_URL` | DB URL 중 택1 | - | 위 변수의 별칭 |
| `OBSIDIAN_SYNC_API_TOKEN` | admin token 중 택1 | - | Admin token |
| `KNOWLEDGE_API_TOKEN` | admin token 중 택1 | - | 위 변수의 별칭 |
| `OBSIDIAN_SYNC_OLLAMA_BASE_URL` | 아니오 | `http://localhost:11434` | Ollama 주소 |
| `OBSIDIAN_SYNC_EMBEDDING_MODEL` | 아니오 | `bge-m3` | 임베딩 모델 |
| `OBSIDIAN_SYNC_EMBEDDING_DIMENSION` | 아니오 | `1024` | 벡터 차원 |
| `OBSIDIAN_SYNC_VAULT_STORAGE_ROOT` | 아니오 | `vaults` | Vault 저장 루트 |
| `OBSIDIAN_SYNC_VAULT_ARCHIVE_ROOT` | 아니오 | `archives` | 아카이브 루트 |
| `OBSIDIAN_SYNC_POST_SYNC_INDEXING_ENABLED` | 아니오 | `true` | 저장 직후 best-effort 인덱싱 예약 |

`postgresql://...` URL은 앱 시작 시 `postgresql+asyncpg://...`로 자동
변환됩니다.

### 마이그레이션

```bash
uv run alembic upgrade head
uv run alembic current
```

초기 마이그레이션이 `vector` 확장을 생성하며, 최신 마이그레이션에는
`knowledge_chunks.embedding`용 HNSW cosine partial index가 포함됩니다.

### 헬스체크

```bash
curl -sS http://localhost:8000/health \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

`/health`는 DB 연결을 검사하지 않습니다. 프로세스·설정과 Ollama 상태를 보는
운영용 엔드포인트입니다. `ollama.status`는 `ok`, `unreachable`, `model_missing`
중 하나이며, Ollama가 정상이 아니면 최상위 `status`가 `degraded`가 됩니다.

### 백업

```bash
uv run python scripts/backup_vaults.py \
  --vault-root vaults \
  --archive-root archives \
  --backup-dir backups
```

`--vault-id`로 특정 vault만 백업할 수 있습니다. PostgreSQL 백업은 별도이며
`pg_dump`를 사용하세요.

### 파일 정책

| 종류 | 확장자 | 크기 상한 |
| --- | --- | --- |
| Markdown | `.md` | 2 MB |
| 이미지 | `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp` | 10 MB |
| PDF | `.pdf` | 30 MB |

Markdown만 벡터화 대상입니다. `.env`, `.key`, `.pem`, `.zip`, `.exe`, `.db` 등은
명시적으로 차단됩니다. 경로는 상대 POSIX 경로여야 하며 절대 경로, 역슬래시,
`.`·`..` 탐색은 거부됩니다.

### API 요약

Admin token 전용:

| Method | Path | 설명 |
| --- | --- | --- |
| `GET` | `/health` | 프로세스·설정·Ollama 상태 |
| `POST` `GET` `DELETE` | `/tokens` , `/tokens/{token_id}` | DB API token 발급·조회·폐기 |

DB API token:

| Method | Path | 설명 |
| --- | --- | --- |
| `POST` `GET` | `/vaults` | Vault 생성·목록 |
| `POST` | `/vaults/{vault_id}/sync/devices` | 디바이스 등록 |
| `GET` | `/vaults/{vault_id}/sync/changes` | cursor 이후 변경 조회 |
| `GET` | `/vaults/{vault_id}/sync/status` | 동기화 상태 |
| `GET` `PUT` `DELETE` | `/vaults/{vault_id}/files/{path}` | 파일 조회·업로드(409 충돌)·soft delete |
| `POST` | `/vaults/{vault_id}/sync/restore` | 삭제된 파일 복구 |
| `POST` | `/vaults/{vault_id}/reindex` , `/reindex/file` | 재인덱싱 |
| `POST` | `/knowledge/search` | 의미 검색 |
| `GET` | `/search-logs` | 검색 로그 |
| `*` | `/mcp/*` | 에이전트용 엔드포인트 → [docs/AGENT.md](docs/AGENT.md) |

전체 요청·응답 예시와 revision 정책은 [docs/sync-api.md](docs/sync-api.md)에
있습니다.

---

## 문서

| 문서 | 내용 |
| --- | --- |
| [docs/CLI.md](docs/CLI.md) | `obsisync` 설치·설정·명령어·충돌 해결·문제 해결 |
| [docs/AGENT.md](docs/AGENT.md) | MCP 연동, tool 목록, 스킬, 프론트매터 규격 |
| [docs/sync-api.md](docs/sync-api.md) | 서버 REST API 계약, revision 정책 |
| [docs/sync-agent.md](docs/sync-agent.md) | 에이전트 내부 동작(manifest, sync 순서, conflict 포맷) |
| [docs/vectorizing.md](docs/vectorizing.md) | 청킹·임베딩·인덱싱 동작 |
| [AGENTS.md](AGENTS.md) | 개발자용: 구조, 빌드·테스트 명령, 코딩 규약 |

기여와 로컬 개발 환경 구성은 [AGENTS.md](AGENTS.md)를 참고하세요.
