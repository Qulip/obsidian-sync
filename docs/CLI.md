# obsisync CLI

`obsisync`는 각 PC에서 실행하는 로컬 동기화 클라이언트입니다. 로컬 Obsidian
Vault와 서버의 Canonical Vault를 revision 기반으로 양방향 동기화합니다.

이 문서는 **사용법 레퍼런스**입니다. manifest 구조, sync 순서, conflict 파일
포맷 같은 내부 동작은 [sync-agent.md](sync-agent.md)를, 서버 API 계약은
[sync-api.md](sync-api.md)를 참고하세요.

---

## 설치

### 자동 설치 (권장)

릴리스에서 현재 OS/CPU용 바이너리를 내려받아 체크섬을 검증하고 설치합니다.

```bash
# macOS / Linux
curl -fsSL https://raw.githubusercontent.com/Qulip/obsidian-sync/main/install.sh | bash
```

```powershell
# Windows
irm https://raw.githubusercontent.com/Qulip/obsidian-sync/main/install.ps1 | iex
```

설치 스크립트는 바이너리 설치 외에 knowledge-management 스킬 설치와 MCP 설정
수집도 함께 진행합니다(둘 다 건너뛸 수 있음). 자세한 내용은
[AGENT.md](AGENT.md)를 참고하세요.

기본 설치 경로:

| 플랫폼 | 경로 |
| --- | --- |
| macOS / Linux | `$HOME/.local/bin/obsisync` |
| Windows | `%LOCALAPPDATA%\Programs\obsisync` |

`OBSIDIAN_SYNC_AGENT_INSTALL_DIR` 환경변수로 설치 경로를 바꿀 수 있습니다.
설치 경로가 `PATH`에 없으면 스크립트가 안내 메시지를 출력합니다.

지원 플랫폼: darwin/arm64, darwin/amd64, linux/amd64, linux/arm64,
windows/amd64.

> 설치 스크립트는 항상 **최신 릴리스**를 내려받고, 같은 릴리스에 함께 게시된
> `checksums.txt`로 무결성을 검증합니다. 이후 갱신은 `obsisync update`로 합니다.

### 소스에서 빌드

Go 1.23+가 필요합니다.

```bash
make build-agent
install dist/obsisync/obsisync /usr/local/bin/obsisync
obsisync --help
```

모든 타깃을 한 번에 빌드하려면 `make build-agent-all`을 실행합니다. 산출물은
git에서 무시되는 `dist/obsisync/` 아래에 생성됩니다.

### 업데이트

```bash
obsisync update
```

최신 GitHub Release를 확인한 뒤 설치 여부를 물어봅니다.

### Python CLI (호환용)

Go 바이너리가 배포·운영 경로이지만, Python 콘솔 스크립트도 호환성 확인과
롤백을 위해 유지됩니다.

```bash
uv sync
uv run obsidian-sync-agent --help
```

---

## 사전 준비

CLI를 쓰기 전에 서버 쪽에서 두 가지가 준비되어야 합니다.

1. **Vault 생성** — `POST /vaults`
2. **DB API token 발급** — `POST /tokens` (admin token이 아닌, 발급받은 client token)

두 작업 모두 [README의 빠른 시작](../README.md#2-vault와-토큰-준비)에 설명되어
있습니다.

---

## 설정

설정 우선순위는 **CLI 인자 > 환경변수 > 설정 파일 > 기본값**입니다.
설정 파일 위치는 `{vault_root}/.obsidian-sync-agent/config.json`입니다.

### 필수 설정

| 항목 | CLI | 환경변수 | Config 키 | 기본값 |
| --- | --- | --- | --- | --- |
| 서버 주소 | `--server` | `OBSIDIAN_SYNC_AGENT_SERVER` | `server_base_url` | (필수) |
| Vault ID | `--vault-id` | `OBSIDIAN_SYNC_AGENT_VAULT_ID` | `vault_id` | (필수) |
| API 토큰 | - | `OBSIDIAN_SYNC_AGENT_TOKEN` | - | (필수) |

> **API 토큰은 환경변수로만 받습니다.** 설정 파일이나 로그에 저장되지 않습니다.

### 선택 설정

| 항목 | CLI | 환경변수 | Config 키 | 기본값 |
| --- | --- | --- | --- | --- |
| Vault 경로 | `--vault-root` | `OBSIDIAN_SYNC_AGENT_VAULT_ROOT` | - | 현재 디렉터리 |
| Device ID | `--device-id` | `OBSIDIAN_SYNC_AGENT_DEVICE_ID` | `device_id` | hostname |
| Device 이름 | - | `OBSIDIAN_SYNC_AGENT_DEVICE_NAME` | `device_name` | - |
| 충돌 정책 | `--conflict-policy` | `OBSIDIAN_SYNC_AGENT_CONFLICT_POLICY` | `conflict_policy` | `manual` |
| 첨부파일 동기화 | `--sync-attachments` / `--no-sync-attachments` | `OBSIDIAN_SYNC_AGENT_SYNC_ATTACHMENTS` | `sync_attachments` | `false` |
| 첨부 크기 상한(byte) | `--attachment-max-bytes` | `OBSIDIAN_SYNC_AGENT_ATTACHMENT_MAX_BYTES` | `attachment_max_bytes` | `30000000` |
| watch debounce(초) | `--watch-debounce-seconds` | `OBSIDIAN_SYNC_AGENT_WATCH_DEBOUNCE_SECONDS` | `watch_debounce_seconds` | `2.0` |
| watch 안전망 주기(초) | `--watch-interval-seconds` | `OBSIDIAN_SYNC_AGENT_WATCH_INTERVAL_SECONDS` | `watch_interval_seconds` | `0.0` (비활성) |
| Obsidian refresh 강제 | `--require-obsidian-refresh` | - | `require_obsidian_refresh` | `false` |
| 디버그 로그 | `--verbose` | - | - | `false` |

### 환경변수만으로 실행

```bash
export OBSIDIAN_SYNC_AGENT_SERVER='http://localhost:8000'
export OBSIDIAN_SYNC_AGENT_VAULT_ID='personal-main'
export OBSIDIAN_SYNC_AGENT_TOKEN='osk_...'

obsisync sync --vault-root "$HOME/ObsidianVault"
```

### 설정 파일 예시

`~/ObsidianVault/.obsidian-sync-agent/config.json`:

```json
{
  "server_base_url": "http://localhost:8000",
  "vault_id": "personal-main",
  "device_id": "macbook-pro",
  "device_name": "MacBook Pro",
  "conflict_policy": "manual",
  "sync_attachments": false,
  "watch_debounce_seconds": 2.0,
  "obsidian": {
    "enabled": false,
    "base_url": "https://127.0.0.1:27124",
    "verify_tls": false,
    "reload_command": false
  }
}
```

토큰은 이 파일에 넣지 마세요. 환경변수로만 전달됩니다.

---

## 명령어

### `obsisync sync`

한 번의 동기화 사이클(pull → 로컬 반영 → scan → push)을 실행합니다.

```bash
obsisync sync --vault-root "$HOME/ObsidianVault"
```

주요 플래그:

| 플래그 | 설명 |
| --- | --- |
| `--dry-run` | 실제 쓰기/전송 없이 계획만 출력 |
| `--conflict-policy` | `manual`(기본), `local-wins`, `remote-wins` |
| `--sync-attachments` | 이미지·PDF 첨부 동기화 활성화 |
| `--attachment-max-bytes` | 로컬 첨부 크기 필터(byte) |
| `--require-obsidian-refresh` | Obsidian refresh 실패 시 non-zero 종료 |
| `--verbose` | 디버그 로그 |

주기 실행은 cron / launchd / systemd timer로 이 명령을 반복 호출하면 됩니다.

### `obsisync watch`

파일 변경을 감시하며 자동으로 동기화합니다. 변경이 멈춘 뒤
`--watch-debounce-seconds`(기본 2초)만큼 조용해지면 sync를 실행합니다.

```bash
# 기본 watch
obsisync watch --vault-root "$HOME/ObsidianVault"

# debounce를 짧게, 10분 주기 안전망 추가
obsisync watch --vault-root "$HOME/ObsidianVault" \
  --watch-debounce-seconds 0.5 \
  --watch-interval-seconds 600
```

`--watch-interval-seconds`는 파일 이벤트를 놓쳤을 때를 대비한 주기적 안전망
동기화입니다. `0`(기본값)이면 비활성입니다. `sync`와 동일한 동기화 플래그를
모두 지원합니다.

### `obsisync status`

서버와 로컬의 동기화 상태를 출력합니다.

```bash
obsisync status --vault-root "$HOME/ObsidianVault"
```

### `obsisync update`

설치된 CLI를 최신 GitHub Release로 갱신합니다.

```bash
obsisync update
```

---

## 종료 코드

| 코드 | 의미 |
| --- | --- |
| `0` | sync 성공 (또는 `--dry-run` 완료) |
| `1` | sync는 완료됐지만 conflict 발생 → `.conflict` 파일 확인 필요 |
| `2` | 설정 오류 또는 sync 실패 (서버 연결 불가 등) |
| `3` | `--require-obsidian-refresh` 지정 시 Obsidian refresh 단계 실패 |

cron 스크립트에서 `1`과 `2`를 구분해 처리하면 "충돌 발생"과 "동기화 실패"를
다르게 알림 처리할 수 있습니다.

---

## 충돌 해결

같은 파일이 다른 PC에서 먼저 수정되어 서버 revision이 앞서 있으면 push가
409로 거부됩니다. 처리 방식은 `--conflict-policy`로 선택합니다.

### `manual` (기본값)

로컬에 conflict 파일을 만들고 사용자가 직접 해결하게 합니다.

```text
Notes/JPA.conflict.macbook-pro.20260707-121500.md
```

이 파일에는 로컬 버전과 서버 버전이 모두 담겨 있습니다.

1. `.conflict` 파일을 열어 두 버전을 비교하고, 원본(`Notes/JPA.md`)을 원하는
   내용으로 직접 정리합니다.
2. `.conflict` 파일을 삭제합니다.
3. 다시 `obsisync sync`를 실행합니다.

다음 sync는 conflict 당시의 서버 revision을 기준으로 정리된 원본을 업로드합니다.
원본 파일을 바꾸지 않은 상태에서는 unresolved conflict로 보고 다시 push하지
않습니다.

### `local-wins` / `remote-wins`

자동 해결 정책입니다. `local-wins`는 로컬 내용을 서버에 덮어쓰고,
`remote-wins`는 서버 내용을 로컬에 반영합니다(로컬 변경은 백업 후 폐기).
단일 사용자·다중 기기 환경에서 유용합니다. 동작 세부사항은
[sync-agent.md의 자동 해결 정책](sync-agent.md#자동-해결-정책-local-wins--remote-wins)을
참고하세요.

`.conflict` 파일은 동기화·벡터화 대상에서 제외되므로 서버로 올라가거나
검색 결과에 포함되지 않습니다.

---

## 동기화 대상

기본적으로 **`.md` 파일만** 동기화합니다.

`--sync-attachments`를 켜면 이미지와 PDF도 동기화합니다.

| 종류 | 확장자 | 크기 상한 |
| --- | --- | --- |
| Markdown | `.md` | 2 MB |
| 이미지 | `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp` | 10 MB |
| PDF | `.pdf` | 30 MB |

첨부파일은 임베딩·검색 대상이 아닙니다.

### 제외되는 항목

`.obsidian/`, `.obsidian-sync-agent/`, `.trash/`, `.DS_Store`, `Thumbs.db`,
`*.conflict.*.md`, `*.sync-conflict*.md`, 그 밖의 숨김 파일.

---

## 로컬 상태 파일

에이전트는 Vault 안 숨김 디렉터리에 상태를 보관합니다. 이 디렉터리는 동기화와
벡터화 대상에서 제외됩니다.

| 파일 | 역할 |
| --- | --- |
| `{vault_root}/.obsidian-sync-agent/manifest.json` | 마지막 sync cursor, 파일별 revision/hash |
| `{vault_root}/.obsidian-sync-agent/config.json` | 선택적 설정 파일 |

manifest가 손상되면 삭제하세요. 다음 sync에서 서버 상태를 기준으로
재구성됩니다.

---

## Obsidian Local REST API 연동 (선택)

Obsidian이 실행 중이면 로컬 디스크 변경을 앱이 file watcher로 자동 인식하므로
별도 연동 없이도 동작합니다.

연동을 켜면(`obsidian.enabled: true`) 에이전트가 Local REST API에 연결 확인을
시도하고, `reload_command: true`일 때만 앱 reload를 실행합니다. reload는 저장하지
않은 편집 내용을 잃을 수 있어 기본값이 off입니다.

Local REST API 키는 `OBSIDIAN_LOCAL_REST_API_KEY` 환경변수로 전달합니다.
연동 실패는 기본적으로 warning이며 sync 성공에 영향을 주지 않습니다
(`--require-obsidian-refresh`를 주면 종료 코드 `3`으로 실패 처리).

자세한 내용은 [sync-agent.md](sync-agent.md#obsidian-local-rest-api-연동)를
참고하세요.

---

## 문제 해결

| 증상 | 확인할 것 |
| --- | --- |
| 종료 코드 `2`, 연결 실패 | `--server` 주소, 서버 기동 여부, 방화벽 |
| 401 Unauthorized | `OBSIDIAN_SYNC_AGENT_TOKEN`이 DB API token인지 (admin token은 거부됨) |
| 404 Vault not found | `--vault-id`가 서버에 생성된 vault인지 |
| 종료 코드 `1` 반복 | 남아 있는 `.conflict` 파일 확인 후 원본 정리·삭제 |
| 일부 파일이 동기화 안 됨 | 확장자·크기 제한, 제외 패턴, `--sync-attachments` 여부 |
| 동기화 상태가 꼬임 | `manifest.json` 삭제 후 재실행 |
| 무엇이 올라갈지 모르겠음 | `--dry-run`으로 계획 확인 |

`--verbose`를 붙이면 각 단계의 디버그 로그를 볼 수 있습니다.
