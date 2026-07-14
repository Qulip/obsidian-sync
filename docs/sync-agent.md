# Sync Agent

`obsisync`는 로컬 Obsidian vault와 obsidian-sync 서버를 동기화하는 Go CLI 도구입니다.
`.md` 파일은 항상 동기화합니다. 첨부 파일(이미지·PDF)은 `sync_attachments` 설정을 켜야
동기화됩니다(기본값 `false`, 하위 호환) — 자세한 내용은
[첨부파일 동기화](#첨부파일-동기화) 섹션을 참고하세요.

---

## 설치

권장 클라이언트 배포 형태는 Go로 빌드한 `obsisync` 단일
바이너리입니다. 서버는 여전히 Python FastAPI 애플리케이션으로 실행합니다.

릴리스에 현재 플랫폼용 바이너리가 첨부되어 있다면 내려받아 실행 권한을 주고
`PATH`에 둡니다. 아직 실제로 게시되지 않은 릴리스 산출물은 이 문서에서
가용하다고 보장하지 않습니다.

```bash
make build-agent
install dist/obsisync/obsisync /usr/local/bin/obsisync
obsisync --help
```

크로스 플랫폼 로컬 빌드:

```bash
make build-agent-all
```

산출물은 git에서 무시되는 `dist/obsisync/` 아래에 생성됩니다.

기존 Python CLI 진입점은 아직 유지됩니다. Go 바이너리가 클라이언트 배포 경로가
되지만, Python `obsidian-sync-agent` 콘솔 스크립트는 명시적인 제거 작업 전까지
호환성 확인과 롤백을 위해 남아 있습니다.

```bash
uv sync --dev
```

Python entry point:

```bash
uv run obsidian-sync-agent --help
```

서브 커맨드:

```
obsisync sync                [옵션]   # 전체 sync 사이클 실행
obsisync status              [옵션]   # 서버 및 로컬 상태 출력
obsisync update                        # 최신 Release 확인 및 설치
obsidian-sync-agent watch   [옵션]   # 포그라운드 상주: 파일 변경 감지 후 sync 실행
```

`obsisync update`는 GitHub의 최신 안정 Release를 확인하고, 업데이트가 있으면
사용자 확인 후 현재 실행 파일을 교체합니다. Release 자산의 SHA-256 digest가
일치하지 않으면 설치하지 않습니다.

---

## 설정

설정 우선순위: **CLI 인수 > 환경 변수 > config 파일 > 기본값**

Config 파일 위치: `{vault_root}/.obsidian-sync-agent/config.json`

### 핵심 설정 항목

| 항목 | CLI 인수 | 환경 변수 | Config 파일 키 | 기본값 | 필수 |
|---|---|---|---|---|---|
| 서버 URL | `--server` | `OBSIDIAN_SYNC_AGENT_SERVER` | `server_base_url` | — | 필수 |
| Vault ID | `--vault-id` | `OBSIDIAN_SYNC_AGENT_VAULT_ID` | `vault_id` | — | 필수 |
| Vault 루트 경로 | `--vault-root` | `OBSIDIAN_SYNC_AGENT_VAULT_ROOT` | — | 현재 작업 디렉터리 | — |
| Device ID | `--device-id` | `OBSIDIAN_SYNC_AGENT_DEVICE_ID` | `device_id` | 호스트명 | — |
| Device 이름 | — | `OBSIDIAN_SYNC_AGENT_DEVICE_NAME` | `device_name` | — | — |
| API 토큰 | — | `OBSIDIAN_SYNC_AGENT_TOKEN` | — (파일에 저장하지 않음) | — | — |

### Obsidian 연동 설정 (config 파일 `obsidian` 섹션)

| Config 파일 키 | 환경 변수 | 기본값 | 설명 |
|---|---|---|---|
| `obsidian.enabled` | — | `false` | Obsidian 연동 활성화 여부 |
| `obsidian.base_url` | — | `https://127.0.0.1:27124` | Obsidian Local REST API URL |
| `obsidian.api_key` | `OBSIDIAN_LOCAL_REST_API_KEY` | — | API 키 |
| `obsidian.verify_tls` | — | `false` | TLS 인증서 검증 (자체 서명 인증서는 false) |
| `obsidian.reload_command` | — | `false` | `app:reload` 커맨드 실행 여부 |

### 재시도 설정

서버 통신이 일시적으로 실패(네트워크 오류, 5xx, 429 등)하면 지수 백오프로
재시도합니다. 409(SYNC_CONFLICT)는 재시도 대상이 아니며 즉시
[충돌 해결 정책](#충돌-해결-정책)으로 넘어갑니다.

| 항목 | CLI 인수 | 환경 변수 | Config 파일 키 | 기본값 |
|---|---|---|---|---|
| 최대 재시도 횟수 | `--max-retries` | `OBSIDIAN_SYNC_AGENT_MAX_RETRIES` | `max_retries` | `3` |
| 재시도 기본 지연(초) | `--retry-base-delay` | `OBSIDIAN_SYNC_AGENT_RETRY_BASE_DELAY` | `retry_base_delay` | `1.0` |
| 재시도 최대 지연(초) | `--retry-max-delay` | `OBSIDIAN_SYNC_AGENT_RETRY_MAX_DELAY` | `retry_max_delay` | `30.0` |

- 지연 시간은 `retry_base_delay * 2^attempt`(최대 `retry_max_delay`)로 계산되며,
  서버가 `Retry-After` 헤더를 보내면 그 값을 우선 사용합니다(역시
  `retry_max_delay`로 상한).
- `max_retries`는 0 이상이어야 하고, `retry_base_delay`는 0보다 커야 하며,
  `retry_max_delay`는 `retry_base_delay` 이상이어야 합니다. 아니면 설정 오류로
  즉시 실패합니다.

### 첨부파일 동기화 설정

| 항목 | CLI 인수 | 환경 변수 | Config 파일 키 | 기본값 |
|---|---|---|---|---|
| 첨부파일 동기화 활성화 | `--sync-attachments` / `--no-sync-attachments` | `OBSIDIAN_SYNC_AGENT_SYNC_ATTACHMENTS` | `sync_attachments` | `false` |
| 첨부파일 크기 상한(바이트) | `--attachment-max-bytes` | `OBSIDIAN_SYNC_AGENT_ATTACHMENT_MAX_BYTES` | `attachment_max_bytes` | `30000000`(PDF 상한과 동일) |

`sync_attachments`가 `false`(기본값)이면 v1과 동일하게 `.md`만 스캔·push·pull합니다.
자세한 내용은 [첨부파일 동기화](#첨부파일-동기화) 섹션을 참고하세요.

### 충돌 해결 정책

서버와 로컬이 모두 변경되어 409(SYNC_CONFLICT)가 발생했을 때의 처리 방식을
선택합니다. 자세한 동작은 [Conflict 파일](#conflict-파일) 섹션을 참고하세요.

| 항목 | CLI 인수 | 환경 변수 | Config 파일 키 | 기본값 |
|---|---|---|---|---|
| 충돌 해결 정책 | `--conflict-policy` | `OBSIDIAN_SYNC_AGENT_CONFLICT_POLICY` | `conflict_policy` | `manual` |

값은 `manual`, `local-wins`, `remote-wins` 중 하나여야 하며, 그 외의 값은 설정
오류로 즉시 실패합니다.

### watch 모드 설정

Python `obsidian-sync-agent watch`에서만 쓰이는 설정입니다. 자세한 동작은
[watch 모드](#watch-모드) 섹션을 참고하세요.

| 항목 | CLI 인수 | 환경 변수 | Config 파일 키 | 기본값 |
|---|---|---|---|---|
| Debounce 대기 시간(초) | `--watch-debounce-seconds` | `OBSIDIAN_SYNC_AGENT_WATCH_DEBOUNCE_SECONDS` | `watch_debounce_seconds` | `2.0` |
| 주기적 안전망 간격(초) | `--watch-interval-seconds` | `OBSIDIAN_SYNC_AGENT_WATCH_INTERVAL_SECONDS` | `watch_interval_seconds` | `0`(비활성) |

`watch_debounce_seconds`는 0보다 커야 하고, `watch_interval_seconds`는 0 이상이어야
합니다. 아니면 설정 오류로 즉시 실패합니다.

### 기타 설정

| 항목 | CLI 인수 | Config 파일 키 | 기본값 |
|---|---|---|---|
| Obsidian refresh 실패 시 오류 처리 | `--require-obsidian-refresh` | `require_obsidian_refresh` | `false` |
| Dry-run (쓰기 없이 계획만 출력) | `--dry-run` | — | `false` |
| 상세 로그 | `--verbose` | — | `false` |

`OBSIDIAN_SYNC_AGENT_TOKEN`은 보안상 config 파일에 기록하지 않습니다.

### Config 파일 예시

```json
{
  "server_base_url": "http://localhost:8000",
  "vault_id": "personal-main",
  "device_id": "my-laptop",
  "device_name": "My Laptop",
  "require_obsidian_refresh": false,
  "max_retries": 3,
  "retry_base_delay": 1.0,
  "retry_max_delay": 30.0,
  "conflict_policy": "manual",
  "sync_attachments": false,
  "attachment_max_bytes": 30000000,
  "obsidian": {
    "enabled": true,
    "base_url": "https://127.0.0.1:27124",
    "verify_tls": false,
    "reload_command": false
  }
}
```

---

## Manifest

Manifest는 에이전트가 마지막으로 동기화한 파일 상태를 로컬에 기록합니다.

**위치:** `{vault_root}/.obsidian-sync-agent/manifest.json`

**형식:**

```json
{
  "vault_id": "personal-main",
  "device_id": "my-laptop",
  "last_sync_cursor": 42,
  "files": {
    "Notes/example.md": {
      "server_revision": 15,
      "content_hash": "a3f1c2d4e5b6...",
      "last_synced_at": "2026-07-07T12:00:00+00:00"
    },
    "Daily/2026-07-07.md": {
      "server_revision": 16,
      "content_hash": "9b8c7d6e5f4a...",
      "last_synced_at": "2026-07-07T12:01:00+00:00"
    }
  },
  "conflicts": {
    "Daily/2026-07-07.md": {
      "server_revision": 17,
      "server_content_hash": "a1b2c3d4e5f6...",
      "local_content_hash": "9b8c7d6e5f4a...",
      "server_deleted": false
    }
  }
}
```

- `last_sync_cursor`: 마지막으로 처리한 vault revision 번호
- `files[path].server_revision`: 해당 파일의 마지막 동기화 revision (PUT 시 `base_revision`으로 사용)
- `content_hash`: 64자리 hex SHA-256 (서버와 로컬 변경 감지에 사용)
- `conflicts[path]`: 수동 해결 전의 서버 revision과 로컬 해시. 원본 파일이
  이 로컬 해시에서 바뀌면, 다음 push는 conflict 당시 서버 revision을
  `base_revision`으로 사용합니다. `server_deleted=true`이면 로컬 파일 삭제를
  서버 삭제 수락으로 처리합니다.

Manifest 파일은 atomic write로 저장됩니다 (`.tmp` → rename).

---

## 제외 패턴

다음 파일과 디렉터리는 동기화 대상에서 제외됩니다.

| 패턴 | 이유 |
|---|---|
| `.md`도 아니고 허용된 첨부 확장자도 아닌 모든 파일 | 서버 `domain/files.py`의 허용 확장자 목록 밖 |
| `.md`가 아닌 파일 전체 (`sync_attachments: false`, 기본값) | v1 하위 호환 — 첨부파일 동기화 비활성화 상태 |
| `.` 으로 시작하는 디렉터리 (숨김 디렉터리) | 시스템/설정 디렉터리 |
| `.obsidian/` | Obsidian 앱 설정 |
| `.obsidian-sync-agent/` | 에이전트 내부 데이터 |
| `.trash/` | Obsidian 휴지통 |
| `*.conflict.*.{md,png,jpg,jpeg,gif,webp,pdf}` | 에이전트가 생성한 conflict 파일 (확장자별 패턴, `remote-wins`의 `*.local-backup.conflict.*.{ext}` 백업 파일 포함) |
| `*.sync-conflict*.{md,png,jpg,jpeg,gif,webp,pdf}` | 외부 sync 도구(Syncthing 등)의 conflict 파일 |
| `.DS_Store`, `Thumbs.db` | OS 메타데이터 파일 |

위 패턴에 해당하는 경로를 서버에 PUT하려고 하면 400 오류가 반환됩니다.

---

## Sync 순서

`obsisync sync` 실행 시:

```
1. 디바이스 등록 (POST /vaults/{vault_id}/sync/devices)
       |
2. Pull (첫 번째)
   └─ GET /sync/changes?since={last_cursor}
      → 각 변경사항 적용 (아래 "변경 적용 로직" 참조)
      → manifest.last_sync_cursor 갱신
       |
3. 로컬 Vault 스캔
   └─ .md 파일 전체(+ sync_attachments 활성화 시 허용된 첨부파일)를
      SHA-256 해시와 함께 수집
      → manifest와 비교하여 new / modified / deleted 분류
       |
4. Push
   ├─ new:      PUT base_revision=0
   ├─ modified: PUT base_revision={manifest의 server_revision}
   │             (resolved conflict는 conflict.server_revision 사용)
   └─ deleted:  DELETE base_revision={manifest의 server_revision}
       |
5. Pull (두 번째, cursor 전진)
   └─ GET /sync/changes?since={cursor}&device_id={device_id}
      → 자신이 방금 push한 이벤트 포함 처리
      → 디바이스의 last_seen_revision 갱신
       |
6. Manifest 저장
       |
7. Obsidian 연동 단계 실행 (enabled인 경우)
```

**변경 적용 로직:**
- DELETE 이벤트: 로컬 파일이 manifest와 동일한 해시면 삭제, 다르면 conflict 파일 생성
- CREATE/UPDATE/RESTORE 이벤트: 로컬에 미수정 파일이 있으면 덮어쓰기, 수정된 파일이 있으면 conflict 파일 생성
- content_hash가 동일하면 파일 쓰기 없이 revision만 갱신 (no-op)

---

## Conflict 파일

서버와 로컬 모두 변경된 경우(pull 단계) 또는 push한 내용이 서버에서
거부된 경우(push 단계, 409 SYNC_CONFLICT) 에이전트는 `conflict_policy`
설정에 따라 세 가지 방식 중 하나로 해결합니다. 아래 내용은 기본값인
`manual` 정책의 동작이며, `local-wins`/`remote-wins`는
[자동 해결 정책](#자동-해결-정책-local-wins-remote-wins) 섹션을 참고하세요.

`manual`(기본값)에서는 파일을 덮어쓰지 않고 conflict 파일을 생성합니다.

### 파일명 형식

```
{stem}.conflict.{device_id}.{YYYYMMDD-HHMMSS}.md
```

예시: `Notes/example.md` → `Notes/example.conflict.my-laptop.20260707-120000.md`

### Conflict 파일 내용

```markdown
# Sync Conflict

- Path: Notes/example.md
- Device: my-laptop
- Client base revision: 11
- Server revision: 14
- Created at: 2026-07-07T12:00:00+09:00

## Local Version

(로컬 파일의 내용)

## Server Version

(서버 파일의 내용)
```

삭제 이벤트로 인한 conflict인 경우 Server Version 대신 `(file is deleted on the server)`, 로컬 삭제로 인한 경우 `(file was deleted locally)`가 표시됩니다.

### Conflict 파일 중복 방지

conflict 파일은 `(경로, 서버 revision)` 조합당 하나만 생성됩니다.

- pull 단계에서 어떤 경로에 conflict가 기록되면, 같은 실행의 push 단계는 그 경로에 대한 PUT을 시도하지 않습니다. (이미 실패가 확정된 요청이 두 번째 conflict 파일을 만드는 것을 방지)
- conflict 파일을 새로 쓰기 전에, 같은 device·같은 `- Server revision:` 값을 가진 기존 conflict 파일이 있으면 새로 만들지 않고 기존 파일을 재사용합니다.

따라서 서버 revision이 그대로인 채 divergence가 해소되지 않은 상태로 sync를 반복 실행해도 conflict 파일이 계속 누적되지 않습니다. 서버가 다시 갱신되어 새 revision이 되면 그때 새 conflict 파일이 생성됩니다. 서버 revision을 자동으로 채택하는 등의 자동 해결은 수행하지 않습니다.

에이전트는 conflict 당시의 로컬 파일 해시도 manifest에 저장합니다. 원본
파일이 그대로라면 unresolved 상태로 보고 push하지 않습니다. 사용자가 원본
파일을 편집해 해결하면 다음 sync에서 conflict 당시 서버 revision을
`base_revision`으로 사용해 resolved 내용을 서버에 업로드합니다.
서버 삭제 conflict에서 사용자가 원본 파일을 삭제하면 서버 삭제를 수락한
것으로 보고 로컬 manifest만 정리합니다.

### 수동 해결 방법 (`manual` 정책)

1. conflict 파일을 열어 Local Version과 Server Version 비교
2. 원본 파일(`Notes/example.md`)을 올바른 내용으로 편집
3. conflict 파일(`Notes/example.conflict.my-laptop.*.md`) 삭제
4. `obsisync sync` 재실행

Conflict 파일 자체는 제외 패턴(`*.conflict.*.md`)에 의해 서버로 push되지 않습니다.

---

## 자동 해결 정책 (`local-wins` / `remote-wins`)

서버에는 리비전 기반 낙관적 동시성 제어와 `vault_file_versions` 전문 버전
히스토리가 있어, 충돌을 자동으로 해소해도 서버 쪽 이전 내용은 항상 복구
가능합니다. 이를 활용해 `manual` 외에 두 가지 자동 정책을 제공합니다.
정책은 pull 단계 충돌(서버 변경 수신 시 로컬도 변경된 경우)과 push 단계
충돌(내가 보낸 PUT/DELETE가 409로 거부된 경우) 양쪽에 동일하게 적용됩니다.

### `local-wins`

로컬 내용을 승자로 만듭니다. 서버의 최신 revision을 `base_revision`으로
삼아 로컬 내용을 다시 push합니다(파일 삭제 conflict의 경우 서버가
삭제한 상태이므로 `base_revision=0`으로 재생성). 로컬 파일은 건드리지
않습니다.

- 성공하면 conflict 파일을 만들지 않고, manifest를 새 revision으로
  갱신하며, sync는 성공(exit code 0)으로 끝납니다.
- 재push가 다시 409를 받으면(그 사이 다른 기기가 또 변경한 경우), 서버가
  알려준 새 revision을 `base_revision`으로 삼아 다시 시도합니다. 이 재시도는
  최대 2회로 제한되어 있어(연속으로 변경되는 파일에 대해) 무한루프에
  빠지지 않습니다.
- 재시도가 모두 소진되면 `manual` 정책과 동일하게 conflict 파일을 생성하고
  exit code 1(`EXIT_CONFLICTS`)로 끝납니다.

### `remote-wins`

서버 내용을 승자로 만듭니다. 로컬 파일을 서버 내용으로 덮어쓰거나(삭제
conflict의 경우 로컬 파일을 삭제), 로컬 변경은 폐기됩니다.

- **조용한 데이터 손실을 방지하기 위해**, 폐기되는 로컬 내용은 덮어쓰기/삭제
  전에 conflict 파일과 유사한 형식의 로컬 백업 파일로 먼저 저장됩니다.
- 백업 파일명: `{stem}.local-backup.conflict.{device_id}.{YYYYMMDD-HHMMSS}.md`
  (예: `Notes/example.local-backup.conflict.my-laptop.20260711-120000.md`).
  `.conflict.` 마커를 포함하므로 conflict 파일과 마찬가지로 스캔·push·서버
  vectorizing 대상에서 제외됩니다.
- 백업 파일 내용은 `# Sync Conflict (auto-resolved: remote-wins)` 헤더와
  `## Local Version (discarded)` 섹션으로 폐기된 로컬 내용을 담습니다.
- push 단계에서 로컬이 "삭제"를 시도했다가 거부된 경우(서버에 새 내용이
  있는 경우)에는 폐기할 로컬 콘텐츠가 없으므로(파일이 이미 로컬에서
  삭제된 상태) 백업 파일을 만들지 않고 서버 내용을 그대로 복원합니다.
- 성공은 항상 exit code 0으로 끝납니다(재시도가 필요 없는 단발 조작이므로
  `manual`로 폴백하는 경우가 없습니다).

### 로그

두 자동 정책 모두 어떤 파일이 어느 쪽으로 해소됐는지 표준 로그로
기록합니다(예: `conflict on Notes/example.md auto-resolved (local-wins):
pushed local content as revision 6`,
`conflict on Notes/example.md auto-resolved (remote-wins): local changes
backed up to Notes/example.local-backup.conflict.my-laptop....md`).
`local-wins`가 폴백하는 경우 `local-wins could not resolve ...; falling
back to manual resolution` 경고 로그가 먼저 기록됩니다.

---

## 첨부파일 동기화

v2부터 이미지·PDF 첨부파일을 동기화할 수 있습니다. 기본값은 비활성화(`sync_attachments:
false`)이며, 기존 v1 에이전트와 동일하게 `.md`만 동기화합니다.

### 활성화

```json
{
  "sync_attachments": true,
  "attachment_max_bytes": 30000000
}
```

또는 CLI에서:

```bash
obsisync sync --vault-root ~/ObsidianVault --sync-attachments
```

`--no-sync-attachments`로 config/env에서 켜진 값을 CLI에서 다시 끌 수 있습니다.

### 허용 확장자·크기 제한

서버 `domain/files.py`의 기존 정의를 그대로 사용합니다 (에이전트와 서버가 같은 규칙을
공유). 허용 목록 밖 확장자나 명시적으로 제외된 확장자(`.zip`, `.exe`, `.env` 등)는
서버가 `UNSUPPORTED_FILE_TYPE` 400 오류로 거부합니다(fail-closed).

| 종류 | 확장자 | 최대 크기 |
|---|---|---|
| Markdown | `.md` | 2 MB |
| 이미지 | `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp` | 10 MB |
| PDF | `.pdf` | 30 MB |

에이전트의 `attachment_max_bytes` 설정(기본값 30 MB, PDF 상한과 동일)은 **로컬 스캔
단계의 조기 필터**일 뿐입니다 — 이보다 큰 첨부파일은 스캔에서 아예 제외되어 push
시도조차 하지 않고 `sync` 로그에 경고로 남습니다. 실제 종류별 정확한 상한(이미지
10 MB, PDF 30 MB)은 서버가 매 PUT마다 별도로 검증하는 최종 권한이므로,
`attachment_max_bytes`를 더 크게 설정해도 서버 상한을 우회할 수는 없습니다.

### 전송 방식

첨부파일 내용은 기존 push/pull JSON payload의 `content` 필드에 base64로 인코딩되어
전송되며, `encoding: "base64"` 필드로 표시됩니다(`.md`는 기존과 동일하게
`encoding: "utf8"`로 평문 전송). 해시(`content_hash`)는 base64 인코딩 이전의 원본
바이트에 대한 SHA-256이며, 스캐너가 파일을 스캔할 때 계산하는 해시와 동일한 값입니다.

### 충돌 파일 (확장자 유지)

첨부파일은 텍스트가 아니므로 markdown처럼 두 버전을 한 파일에 나란히 표시할 수
없습니다. 대신 원본 확장자를 유지한 채 conflict 파일을 원본 옆에 별도로 생성합니다.

```
{stem}.conflict.{device_id}.{YYYYMMDD-HHMMSS}.{ext}
```

예시: `Images/diagram.png` → `Images/diagram.conflict.my-laptop.20260711-120000.png`

- **manual 정책**: 원본 파일은 그대로 두고, "다른 쪽" 버전(서버 변경 conflict는 서버
  바이트, 서버 삭제 conflict는 로컬 바이트)을 conflict 파일로 저장합니다.
- **local-wins**: markdown과 동일하게 로컬 바이트를 그대로 재push합니다(conflict
  파일을 만들지 않음).
- **remote-wins**: 폐기되는 로컬 바이트를
  `{stem}.local-backup.conflict.{device_id}.{YYYYMMDD-HHMMSS}.{ext}` 파일로 먼저
  백업한 뒤 서버 바이트로 덮어씁니다.
- 두 conflict 파일 패턴 모두 `domain/sync_rules.py`의 허용 확장자별 glob 패턴에
  포함되어 스캔·push·서버 vectorizing 대상에서 자동 제외됩니다.
- markdown의 `- Server revision:` 텍스트 기반 중복 방지와 달리, 첨부파일 conflict는
  내용 해시(SHA-256) 비교로 중복 생성을 방지합니다(같은 경로·디바이스·내용이면 기존
  conflict 파일을 재사용).

### 버전 히스토리 없음 (설계 결정)

서버는 markdown 파일마다 전문(full-text) 버전 히스토리(`vault_file_versions`)를
저장하지만, **첨부파일은 버전 히스토리를 저장하지 않습니다.** 바이너리 첨부는
용량이 크고 텍스트 diff가 불가능해, 리비전마다 전체 사본을 무제한 누적하면 스토리지가
빠르게 폭발할 수 있기 때문입니다. 이 결정의 실질적 영향:

- 첨부파일은 서버 API의 `restore` 엔드포인트로 복구할 수 없습니다 (삭제되거나
  덮어써진 이전 버전이 없음). 복구가 필요하면 로컬 vault 백업이나 `remote-wins`가
  생성한 `.local-backup.conflict.*.{ext}` 파일에 의존해야 합니다.
- vault 파일시스템의 정본(canonical) 사본이 DB의 해시와 어긋나는 극단적 상황에서는
  markdown처럼 이전 버전으로 폴백하지 못하고 500 오류가 됩니다.
- markdown은 이 동작에 영향받지 않습니다 — 기존 버전 히스토리·restore 기능은 그대로
  유지됩니다.

### 임베딩/인덱싱에서 제외

첨부파일은 `knowledge_chunks` 청킹·임베딩 파이프라인에 절대 들어가지 않습니다.
`domain/sync_rules.is_vectorizable_path`가 `.md` 경로만 인덱싱 대상으로 표시하므로,
첨부파일은 항상 `vectorize=false`, `index_status='skipped'`로 기록됩니다.

---

## watch 모드

```bash
obsidian-sync-agent watch --vault-root ~/ObsidianVault
```

`watch`는 cron으로 스케줄링하는 `sync`와 달리 포그라운드에 상주하며
[`watchdog`](https://pypi.org/project/watchdog/)로 vault 전체를 재귀 감시하다가
변경이 있을 때마다 sync를 실행합니다.

### Debounce (일괄 처리)

파일 이벤트는 즉시 처리되지 않습니다. 이벤트가 발생할 때마다 "조용해질 때까지
기다리는" 타이머가 `watch_debounce_seconds`(기본 2초)로 재설정되며, 그 시간 동안
추가 이벤트가 없으면 그때 sync를 한 번 실행합니다. 따라서 대량 붙여넣기·git
체크아웃처럼 짧은 시간에 이벤트가 몰려도 sync는 한 번만 실행됩니다.

### 자기 쓰기 재감지 방지

`run_sync()`는 pull 단계에서 로컬 파일을 쓰고(conflict/backup 파일 생성 포함),
이 쓰기 자체가 다시 파일 이벤트를 발생시켜 push를 트리거하는 무한 루프로
이어질 수 있습니다. watch 모드는 이를 두 단계로 차단합니다.

1. **실행 중 무시**: sync가 실행되는 동안에는 들어오는 모든 이벤트를 버립니다.
2. **실행 직후 드레인**: 파일시스템 이벤트 통지는 실제 쓰기보다 지연되어 도착할
   수 있습니다(특히 macOS FSEvents). sync가 끝난 뒤에도 `watch_debounce_seconds`만큼
   추가로 이벤트를 무시하는 "드레인 구간"을 둬서, sync 종료 직후 뒤늦게 도착하는
   자기 자신의 이벤트가 다음 sync를 트리거하지 않게 합니다.

이 두 플래그만으로 충분한 이유는, manifest(`​.obsidian-sync-agent/`)와
conflict/backup 파일이 애초에 `should_sync` 필터에서 제외되어 감시 대상으로
잡히지 않기 때문입니다 — 가드가 실제로 막아야 하는 대상은 pull이 쓰는 정상
노트/첨부파일 경로뿐입니다.

**트레이드오프**: 드레인 구간(기본 2초) 안에 도착한 "진짜" 외부 편집 이벤트는
큐에 남지 않고 버려집니다. 짧은 구간이라 실무 영향은 작지만, 절대적인 지연
상한이 필요하면 아래 안전망 간격을 0이 아닌 값으로 설정하세요.

### 주기적 안전망

`watch_interval_seconds`(기본 0, 비활성)를 양수로 설정하면 그 시간이 지날 때마다
파일 이벤트가 없어도 sync를 한 번 실행합니다. 서버 쪽에서만 변경이 생겨 로컬
watcher가 아무 이벤트도 못 받는 경우(예: 다른 기기가 push)에 대한 보완책입니다.

### 필터링

감시 대상 필터는 기존 sync 규칙(`should_sync`, `is_ignored_dir`)을 그대로
재사용합니다 — `.md`/첨부파일(설정 시)만 감시하고, conflict/backup 파일,
`.obsidian-sync-agent/`, 숨김 디렉터리는 제외됩니다. [제외 패턴](#제외-패턴)
섹션과 동일합니다.

### 종료 처리

`SIGINT`/`SIGTERM`을 받으면 진행 중인 sync를 끝까지 마친 뒤 깨끗이 종료합니다
(강제 중단하지 않음). 연속으로 sync가 실패해도(네트워크 장애 등) 프로세스는
죽지 않고 지수 백오프(`retry_base_delay` 기준, `retry_max_delay` 상한)로 재시도를
계속하며, 실패는 로그로만 남깁니다 — `sync` 커맨드의 conflict/error 종료 코드는
watch 모드에는 적용되지 않습니다.

### cron/launchd 대비

| 방식 | 장점 | 단점 |
|---|---|---|
| `sync` + cron/launchd 주기 실행 | 상주 프로세스 없음, 자원 소모 적음, 실패해도 다음 스케줄에서 복구 | 변경 후 반영까지 최대 스케줄 주기만큼 지연 |
| `watch` 상주 | 변경 후 수 초 내 반영(준실시간) | 프로세스가 항상 떠 있어야 함(재부팅 시 재시작 필요, 예: launchd `KeepAlive`) |

### 사용 예시

```bash
# 기본 watch (debounce 2초, 안전망 비활성)
obsidian-sync-agent watch --vault-root ~/ObsidianVault

# debounce를 짧게, 10분 안전망 추가
obsidian-sync-agent watch \
  --vault-root ~/ObsidianVault \
  --watch-debounce-seconds 1 \
  --watch-interval-seconds 600
```

---

## 종료 코드

| 코드 | 상수 | 의미 |
|---|---|---|
| `0` | `EXIT_OK` | 성공 또는 dry-run 완료 |
| `1` | `EXIT_CONFLICTS` | Conflict 발생 (`.conflict` 파일 기록됨) |
| `2` | `EXIT_ERROR` | 설정 오류 또는 서버 통신 실패 |
| `3` | `EXIT_OBSIDIAN` | `--require-obsidian-refresh` 지정 시 Obsidian 갱신 실패 |

`conflict_policy`가 `local-wins`/`remote-wins`이고 실제로 자동 해결에
성공한 경우는 `EXIT_CONFLICTS`를 유발하지 않습니다(exit code 0). `manual`
정책의 conflict, 그리고 `local-wins` 재시도가 소진되어 manual로 폴백한
conflict만 `EXIT_CONFLICTS`(1)로 집계됩니다.

---

## Obsidian Local REST API 연동

에이전트는 sync 완료 후 Obsidian Local REST API와 통신하여 파일 변경을 Obsidian이 인식하도록 합니다. 이 기능은 선택적이며 기본값은 비활성화(`enabled: false`)입니다.

### 활성화 방법

config 파일(`{vault_root}/.obsidian-sync-agent/config.json`)에 추가:

```json
{
  "obsidian": {
    "enabled": true,
    "base_url": "https://127.0.0.1:27124",
    "verify_tls": false
  }
}
```

API 키가 필요한 경우 환경 변수로 전달합니다:

```bash
export OBSIDIAN_LOCAL_REST_API_KEY="your-api-key"
```

### TLS 옵션

Obsidian Local REST API는 두 가지 포트를 지원합니다.

| 포트 | 프로토콜 | 인증서 |
|---|---|---|
| `27124` (기본값) | HTTPS | 자체 서명 (`/obsidian-local-rest-api.crt`) |
| `27123` | HTTP | 없음 (플러그인에서 명시적 활성화 필요) |

자체 서명 인증서 환경(기본값)에서는 `verify_tls: false`를 사용합니다.

### 파일 감지 방식

`reload_command: false`(기본값)인 경우 에이전트는 Obsidian의 **파일 감시자(file watcher)**에 의존합니다. Obsidian은 외부에서 변경된 파일을 자동으로 감지합니다(공식 Obsidian 지원 문서 기준). 에이전트는 health probe(`GET /`)만 실행하여 Obsidian이 실행 중인지 확인합니다.

### reload_command 옵션 (주의)

```json
{
  "obsidian": {
    "enabled": true,
    "reload_command": true,
    "api_key": "your-api-key"
  }
}
```

`reload_command: true`를 설정하면 sync 완료 후 `POST /commands/app:reload/`를 실행합니다.

> **경고:** `app:reload`는 **저장되지 않은 편집 내용을 삭제**합니다. 편집 중인 파일이 있을 때는 사용하지 마십시오.

`reload_command`를 사용하려면 인증된 API 키(`api_key`)가 필요합니다. 키가 없으면 ok=False를 반환합니다.

### Obsidian이 닫혀 있을 때

Obsidian이 실행 중이 아니면 health probe(`GET /`)가 실패합니다.

- 기본 동작: 경고 로그 출력 후 정상 완료 (exit code 0)
- `--require-obsidian-refresh` 또는 `require_obsidian_refresh: true` 설정 시: exit code 3 반환

### 문제 해결

**네트워크 드라이브 / Flatpak vault 환경:**

Obsidian의 파일 감시자가 네트워크 드라이브나 Flatpak 샌드박스 환경에서는 외부 변경을 감지하지 못할 수 있습니다. 이 경우 Obsidian 커뮤니티 플러그인 **"Vault File Refresh"** 설치를 권장합니다.

---

## 사용 예시

```bash
# 기본 sync (config 파일 + 환경 변수 사용)
export OBSIDIAN_SYNC_AGENT_TOKEN="osk_..."
obsisync sync --vault-root ~/ObsidianVault

# 변경 계획만 확인 (dry-run)
obsisync sync --vault-root ~/ObsidianVault --dry-run

# 서버 상태 확인
obsisync status \
  --vault-root ~/ObsidianVault \
  --server http://localhost:8000 \
  --vault-id personal-main

# Obsidian refresh 실패 시 오류 처리
obsisync sync \
  --vault-root ~/ObsidianVault \
  --require-obsidian-refresh

# 단일 사용자·다중 기기: 로컬 변경을 항상 우선 (충돌 자동 해결)
obsidian-sync-agent sync \
  --vault-root ~/ObsidianVault \
  --conflict-policy local-wins

# 서버를 항상 진실의 원천으로 (로컬 변경은 백업 후 폐기)
obsidian-sync-agent sync \
  --vault-root ~/ObsidianVault \
  --conflict-policy remote-wins

# 재시도 튜닝 (불안정한 네트워크 환경)
obsidian-sync-agent sync \
  --vault-root ~/ObsidianVault \
  --max-retries 5 \
  --retry-base-delay 2 \
  --retry-max-delay 60
```
