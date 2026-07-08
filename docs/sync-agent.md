# Sync Agent

`obsidian-sync-agent`는 로컬 Obsidian vault와 obsidian-sync 서버를 동기화하는 CLI 도구입니다. `.md` 파일만 동기화하며 (v1), 첨부 파일은 무시합니다.

---

## 설치

```bash
uv sync --dev
```

Entry point:

```bash
uv run obsidian-sync-agent --help
```

서브 커맨드:

```
obsidian-sync-agent sync    [옵션]   # 전체 sync 사이클 실행
obsidian-sync-agent status  [옵션]   # 서버 및 로컬 상태 출력
```

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
| `.md`가 아닌 모든 파일 | v1은 Markdown만 지원 |
| `.` 으로 시작하는 디렉터리 (숨김 디렉터리) | 시스템/설정 디렉터리 |
| `.obsidian/` | Obsidian 앱 설정 |
| `.obsidian-sync-agent/` | 에이전트 내부 데이터 |
| `.trash/` | Obsidian 휴지통 |
| `*.conflict.*.md` | 에이전트가 생성한 conflict 파일 |
| `*.sync-conflict*.md` | 외부 sync 도구(Syncthing 등)의 conflict 파일 |
| `.DS_Store`, `Thumbs.db` | OS 메타데이터 파일 |

위 패턴에 해당하는 경로를 서버에 PUT하려고 하면 400 오류가 반환됩니다.

---

## Sync 순서

`obsidian-sync-agent sync` 실행 시:

```
1. 디바이스 등록 (POST /vaults/{vault_id}/sync/devices)
       |
2. Pull (첫 번째)
   └─ GET /sync/changes?since={last_cursor}
      → 각 변경사항 적용 (아래 "변경 적용 로직" 참조)
      → manifest.last_sync_cursor 갱신
       |
3. 로컬 Vault 스캔
   └─ .md 파일 전체를 SHA-256 해시와 함께 수집
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

서버와 로컬 모두 변경된 경우, 에이전트는 파일을 덮어쓰지 않고 conflict 파일을 생성합니다.

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

### 수동 해결 방법

1. conflict 파일을 열어 Local Version과 Server Version 비교
2. 원본 파일(`Notes/example.md`)을 올바른 내용으로 편집
3. conflict 파일(`Notes/example.conflict.my-laptop.*.md`) 삭제
4. `obsidian-sync-agent sync` 재실행

Conflict 파일 자체는 제외 패턴(`*.conflict.*.md`)에 의해 서버로 push되지 않습니다.

---

## 종료 코드

| 코드 | 상수 | 의미 |
|---|---|---|
| `0` | `EXIT_OK` | 성공 또는 dry-run 완료 |
| `1` | `EXIT_CONFLICTS` | Conflict 발생 (`.conflict` 파일 기록됨) |
| `2` | `EXIT_ERROR` | 설정 오류 또는 서버 통신 실패 |
| `3` | `EXIT_OBSIDIAN` | `--require-obsidian-refresh` 지정 시 Obsidian 갱신 실패 |

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
obsidian-sync-agent sync --vault-root ~/ObsidianVault

# 변경 계획만 확인 (dry-run)
obsidian-sync-agent sync --vault-root ~/ObsidianVault --dry-run

# 서버 상태 확인
obsidian-sync-agent status \
  --vault-root ~/ObsidianVault \
  --server http://localhost:8000 \
  --vault-id personal-main

# Obsidian refresh 실패 시 오류 처리
obsidian-sync-agent sync \
  --vault-root ~/ObsidianVault \
  --require-obsidian-refresh
```
