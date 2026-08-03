# AI 에이전트 연동

Claude Code, Gemini CLI, Codex CLI 같은 코딩 에이전트가 Obsidian Vault를
**장기 기억**으로 사용하도록 연결합니다. 에이전트는 학습한 내용을 노트로
저장하고, 나중에 의미 검색으로 다시 찾아옵니다.

`obsisync` CLI 사용법은 [CLI.md](CLI.md), 서버 REST API 계약은
[sync-api.md](sync-api.md), 인덱싱·임베딩 동작은
[vectorizing.md](vectorizing.md)를 참고하세요.

---

## 필요한 것

| 항목 | 값 |
| --- | --- |
| MCP 엔드포인트 | `<서버주소>/mcp` |
| 인증 헤더 | `Authorization: Bearer <DB API token>` |

> **Admin token은 MCP에서 의도적으로 거부됩니다.** 반드시 `POST /tokens`로
> 발급받은 DB API token(`osk_...`)을 사용하세요.

---

## 1. MCP 서버 등록

서버는 `/mcp`에서 Streamable HTTP MCP를 제공합니다.

### Claude Code

```bash
claude mcp add --transport http obsidian-sync https://sync.example.com/mcp \
  --header "Authorization: Bearer $KNOWLEDGE_API_TOKEN"
```

### 설정 파일을 쓰는 클라이언트

```json
{
  "mcpServers": {
    "obsidian-sync": {
      "type": "http",
      "url": "https://sync.example.com/mcp",
      "headers": {
        "Authorization": "Bearer ${KNOWLEDGE_API_TOKEN}"
      }
    }
  }
}
```

토큰은 MCP 클라이언트가 시크릿으로 주입하는 방식을 권장합니다. 설정 파일에
평문으로 넣지 마세요.

연결이 되면 `list_vaults`를 호출해 확인합니다. 유효한 DB API token이 전달되지
않으면 모든 tool 호출이 401로 실패합니다.

---

## 2. knowledge-management 스킬 설치 (선택)

에이전트가 "언제, 어떤 형식으로" 노트를 저장할지 알려주는 스킬입니다. MCP tool
등록만으로도 동작하지만, 스킬을 함께 설치하면 저장 경로 규칙과 프론트매터
작성까지 일관되게 처리됩니다.

[설치 스크립트](CLI.md#설치)가 대화형으로 설치해 주며, 지원 대상은 다음과
같습니다.

| 선택 | 클라이언트 | 설치 경로 |
| --- | --- | --- |
| 1 | Claude Code | `~/.claude/skills/knowledge-management` |
| 2 | Gemini CLI | `~/.gemini/skills/knowledge-management` |
| 3 | Codex CLI (OpenAI) | `~/.codex/skills/knowledge-management` |
| 4 | Antigravity | `~/.gemini/antigravity/skills/knowledge-management` |
| 5 | Cursor | `~/.cursor/skills/knowledge-management` |
| 6 | Windsurf | `~/.windsurf/skills/knowledge-management` |

스킬은 노트를 `Agent Notes/YYYY-MM-DD-<slug>.md` 경로에 저장하고, MCP tool을 쓸
수 없는 환경에서만 번들된 fallback 스크립트(`list_vaults.py`,
`save_knowledge.py`)를 사용합니다. fallback 스크립트는 `OBSIDIAN_SYNC_URL`과
`KNOWLEDGE_API_TOKEN` 환경변수를 읽습니다.

---

## MCP Tool 목록

| Tool | 역할 |
| --- | --- |
| `list_vaults_mcp_vaults_get` | Vault 목록 조회 |
| `search_knowledge_mcp_knowledge_search_post` | 의미 검색 |
| `sync_file_mcp_vaults__vault_id__sync_file_post` | 노트 한 건 저장 |
| `get_note_mcp_vaults_note_post` | 노트 원문 조회 |
| `reindex_vault_mcp_vaults__vault_id__reindex_post` | 재인덱싱 |
| `get_sync_status_mcp_vaults__vault_id__sync_status_get` | 동기화 상태 조회 |
| `submit_search_feedback_mcp_knowledge_search_feedback_post` | 검색 결과 피드백 |

### 권장 워크플로

1. `list_vaults`로 대상 vault를 고른다.
2. `search_knowledge`로 기존 지식을 먼저 찾는다.
3. 결과의 `source_path`, `heading_path`, `content`, `agent_hint`를 활용한다.
4. 새로 배운 내용은 `sync_file`로 저장한다.
5. 저장 직후 검색이 필요하면 post-sync 인덱싱이 끝날 시간을 준 뒤 검색한다.
   프로세스가 재시작됐거나 인덱싱이 실패했다면 `reindex_vault`를
   `mode=changed_only`로 호출한다.

### `sync_file`의 fail-closed 동작

같은 경로에 **내용이 다른** 노트가 이미 있으면 `409 CONFLICT_DETECTED`를
반환하고 덮어쓰지 않습니다. 의도적으로 교체하려면 `overwrite: true`를 함께
보냅니다. 내용이 동일하면 `overwrite` 값과 무관하게 `status: "skipped"`로
no-op 처리됩니다.

---

## REST MCP API

MCP 클라이언트를 쓰지 않고 일반 HTTP로도 같은 기능을 호출할 수 있습니다.
`/mcp/*` 아래의 평범한 REST 엔드포인트이며, 동일하게 DB API token으로
보호됩니다.

Vault 목록:

```bash
curl -sS https://sync.example.com/mcp/vaults \
  -H "Authorization: Bearer $API_TOKEN"
```

노트 저장:

```bash
curl -sS -X POST https://sync.example.com/mcp/vaults/personal-main/sync/file \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "path": "Agent Notes/2026-08-03-example.md",
    "content": "---\ntitle: Agent Example\ntype: study-note\nproject: obsidian-sync\ndomain: backend\nstatus: current\npriority: medium\nvisibility: personal\ntags: [agent]\nvectorize: true\ncreated: 2026-08-03\nupdated: 2026-08-03\n---\n# Agent Example\n\nSaved by an agent.\n",
    "mime_type": "text/markdown"
  }'
```

검색:

```bash
curl -sS -X POST https://sync.example.com/mcp/knowledge/search \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"vault_id":"personal-main","query":"deployment checklist","top_k":5}'
```

| Method | Path | 설명 |
| --- | --- | --- |
| `GET` | `/mcp/vaults` | Vault 목록 |
| `POST` | `/mcp/vaults/{vault_id}/sync/file` | 노트 저장 |
| `POST` | `/mcp/vaults/note` | 노트 조회 |
| `POST` | `/mcp/knowledge/search` | 의미 검색 |
| `POST` | `/mcp/knowledge/search/feedback` | 검색 피드백 |
| `POST` | `/mcp/vaults/{vault_id}/reindex` | 재인덱싱 |
| `GET` | `/mcp/vaults/{vault_id}/sync/status` | 동기화 상태 |

---

## 검색

```bash
curl -sS -X POST https://sync.example.com/knowledge/search \
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

기본값: `top_k` 5, 최대 `top_k` 10, 기본 status 필터 `current`.

결과에는 `source_path`, `title`, `heading_path`, `content`, `agent_hint`와
`project` / `domain` / `type` / `priority` / `visibility` / `tags`가 포함됩니다.

검색 로그는 자동 저장되며 `GET /search-logs?limit=20`으로 조회할 수 있습니다.

---

## Markdown 프론트매터

**벡터화 대상 Markdown은 반드시 프론트매터로 시작해야 합니다.** 누락되거나
값이 유효하지 않으면 인덱싱이 실패하고 `index_failure_logs`에 기록됩니다.

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
created: 2026-08-03
updated: 2026-08-03
---
```

모든 필드가 필수입니다.

| 필드 | 허용 값 |
| --- | --- |
| `title` | 자유 문자열 |
| `type` | `reference`, `rule`, `decision`, `issue-solution`, `study-note`, `prompt`, `command`, `checklist` |
| `project` | 자유 문자열 |
| `domain` | 자유 문자열 |
| `status` | `current`, `draft`, `deprecated`, `archived` |
| `priority` | `high`, `medium`, `low` |
| `visibility` | `personal`, `company`, `confidential`, `public` |
| `tags` | 문자열 배열 |
| `vectorize` | `true`, `false` |
| `created`, `updated` | `YYYY-MM-DD` |

`vectorize: false`인 노트는 저장·동기화되지만 임베딩·검색 대상에서 제외됩니다.

---

## 인덱싱 시점

기본 설정(`OBSIDIAN_SYNC_POST_SYNC_INDEXING_ENABLED=true`)에서는 성공한
Markdown 저장·복구가 파일을 `pending`으로 표시한 뒤, 같은 서버 프로세스 안에서
best-effort 인덱싱을 예약합니다.

이 작업은 응답을 막지 않지만 **durable queue가 아닙니다.** 프로세스가
재시작되면 대기 중이던 작업은 사라질 수 있고, 멀티 프로세스 전달이나
exactly-once 처리를 보장하지 않습니다. 이 경우에도 `pending` 행은 DB에 남아
있으므로 재인덱싱으로 복구할 수 있습니다.

```bash
# 변경분만
curl -sS -X POST https://sync.example.com/vaults/personal-main/reindex \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"mode":"changed_only"}'

# 전체 재구축
curl -sS -X POST https://sync.example.com/vaults/personal-main/reindex \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"mode":"full"}'

# 파일 하나
curl -sS -X POST https://sync.example.com/vaults/personal-main/reindex/file \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"path":"Notes/example.md"}'
```

---

## 문제 해결

| 증상 | 확인할 것 |
| --- | --- |
| 모든 tool 호출이 401 | admin token을 쓰고 있지 않은지 (MCP는 DB API token만 허용) |
| 저장은 됐는데 검색에 안 나옴 | 인덱싱 대기 중일 수 있음 → `reindex_vault`를 `changed_only`로 호출 |
| 인덱싱이 계속 실패 | 프론트매터 필수 필드·허용 값 확인, `index_failure_logs` 조회 |
| `sync_file`이 409 반환 | 같은 경로에 다른 내용이 이미 있음 → 의도적 교체면 `overwrite: true` |
| 검색 결과 품질이 낮음 | Ollama·`bge-m3` 상태를 `GET /health`의 `ollama.status`로 확인 |
