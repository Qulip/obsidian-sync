> Historical prompt: 이 파일은 양방향 sync 기능을 구현하기 위해 작성된
> 일회성 작업 프롬프트입니다. 현재 harness 지침이나 최신 API 계약으로
> 사용하지 마세요. 최신 동기화 정책은 `README.md`, `docs/sync-api.md`,
> `docs/sync-agent.md`, `docs/vectorizing.md`를 기준으로 합니다.

이번 작업의 목표는 **자동 병합 없는 API Sync**를 설계하고 구현하는 것이다.

작업 진행 방식의 중요한 원칙은 다음과 같다.

- 메인 에이전트는 직접 구현을 최소화한다.
- 메인 에이전트는 계획, 작업 분해, 서브에이전트 위임, 결과 통합, 검증 판단, 최종 보고만 담당한다.
- 실제 코드 조사, 리팩토링, 테스트 작성, E2E 검증, 리뷰는 가능한 한 서브에이전트에게 위임한다.
- 토큰 관리를 위해 긴 파일 탐색, 로그 분석, 테스트 실패 원인 분석은 반드시 서브에이전트에서 수행하고, 메인 에이전트는 요약 결과만 받아 판단한다.
- 단순히 “수정했습니다”라고 끝내지 말고, 빌드/타입체크/테스트/E2E 검증 증거가 확보될 때까지 작업을 계속 진행한다.
- 작업 도중 불확실한 부분이 있어도 치명적인 제품 의사결정이 아니라면 합리적인 가정을 명시하고 계속 진행한다.
- 사용자에게 질문해야 하는 경우는 다음에 한정한다.
  - 인증정보, 외부 API 키, 계정 권한이 없어서 진행 불가능한 경우
  - 파괴적 변경이 필요하지만 명확한 승인 기준이 없는 경우
  - 제품 요구사항 자체가 서로 충돌하는 경우

서비스의 중요한 정책은 다음과 같다.

- 서버의 Canonical Vault를 정본으로 본다.
- 각 PC는 로컬 Obsidian Vault를 가지고 있다.
- 각 PC에는 별도 sync-agent 또는 CLI/데몬이 존재할 수 있다.
- Obsidian Sync를 직접 재구현하려 하지 않는다.
- 자동 병합은 구현하지 않는다.
- 동일 파일이 여러 PC에서 동시에 수정되면 서버는 409 Conflict를 반환한다.
- Conflict가 발생하면 API는 실패하고, 사용자가 직접 확인/해결한다.
- Conflict 파일은 벡터화하지 않는다.
- 삭제는 즉시 hard delete 하지 않고 soft delete 한다.
- Vector DB는 원본이 아니라 Markdown Vault에서 파생된 검색 인덱스로 취급한다.
- 모든 검색 API와 MCP Tool은 반드시 vault_id를 받아야 한다.
- 개인용 서비스이므로 검색 로그와 sync 로그는 저장한다.
- 토큰 권한은 세분화하지 않아도 되지만, 기존 인증은 유지한다.

---

# 프롬프트 해석 기준

이 문서는 기능 추가 범위를 정의한다. 구현자는 아래 기준을 우선 적용해라.

- 기존 FastAPI 구조, ResponseEnvelope 응답 형식, 인증 dependency, repository/service 계층을 유지한다.
- 기존 `/vaults/{vault_id}/sync/manifest`, `/sync/files`, `/sync/archive`, `/reindex` 흐름은 깨지지 않게 유지한다.
- 새 revision 기반 sync API는 기존 단순 업로드 API를 대체하기보다 확장하는 방향을 우선 검토한다.
- 기존 DB 테이블명이 `vault_files`처럼 복수형이면 새 문서의 `vault_file` 개념은 기존 테이블 확장 또는 별도 테이블 추가로 해석한다. 테이블명을 억지로 바꾸지 않는다.
- 서버의 Canonical Vault 원문 저장소는 현재 프로젝트 방식과 맞춘다. 현재 파일 시스템 저장소가 정본이면 파일 시스템을 원문 정본으로 유지하고, DB는 metadata, revision, event, version history를 관리한다.
- 기존 archive 기능은 새 soft delete 정책과 충돌하지 않게 정리한다. 사용자 삭제 API는 즉시 archive/hard delete하지 않고 deleted 상태와 tombstone event를 남겨야 한다.
- 검색/색인은 파생 데이터다. API sync commit과 version history 저장이 성공한 뒤에만 vectorizing 대상이 된다.
- 기능 요구사항을 줄이거나 바꾸지 말고, 구현 세부가 모호한 경우 이 문서의 “명확화된 정책”을 따른다.

## 명확화된 정책

- `revision`은 우선 vault 단위 전역 증가값으로 관리한다.
- 파일의 현재 revision은 해당 파일에 마지막으로 적용된 vault revision으로 본다.
- `base_revision`은 클라이언트가 알고 있는 “해당 파일의 현재 revision”이다. 다른 파일의 변경으로 vault 전역 revision이 증가해도 같은 파일의 revision이 같으면 conflict로 보지 않는다.
- 신규 파일 생성은 `base_revision: 0`으로 요청한다. 서버에 같은 path의 활성 파일이 이미 있으면 409 Conflict를 반환한다.
- 삭제된 파일을 같은 path로 다시 생성하는 경우는 restore가 아니라 새 revision의 CREATE/UPDATE로 처리할지, restore API만 허용할지 구현 전에 명시적으로 선택하고 문서화한다.
- `content_hash` 형식은 기존 코드와 맞춘다. 기존 시스템이 `sha256:<hex>` prefix를 쓰지 않으면 prefix 없이 64자리 hex를 사용해도 되지만, API 문서와 테스트에서 하나로 고정한다.
- `path`는 vault root 기준 POSIX 상대 경로다. 선행 `/`, `..`, 빈 segment, OS별 separator 혼용, URL-decoding 후 traversal을 모두 차단한다.
- URL path parameter로 slash 포함 path를 받을 때는 FastAPI path converter 또는 query/body field 중 하나를 선택한다. 선택한 방식은 API 문서와 테스트에 명시한다.
- response 예시는 개념 예시다. 실제 응답은 기존 프로젝트의 envelope 형식을 유지한다.
- Local REST API 연동은 optional이다. 이 연동 실패는 sync commit 성공/실패 판정과 분리한다.
- sync log는 sync_event와 별도 운영 로그 중 기존 구조에 맞는 방식으로 남기되, token과 Markdown 원문은 기본 로그에 남기지 않는다.

---

# 먼저 해야 할 일

코드를 수정하기 전에 반드시 아래 순서로 조사하고 분석해라.

## 1. 기존 프로젝트 분석

먼저 현재 프로젝트 구조를 분석해라.

확인할 것:

- Backend framework
- API 구조
- DB 접근 방식
- Vector DB 저장 방식
- Markdown 생성 로직
- Obsidian Vault 저장 경로 관리 방식
- MCP Server 구현 방식
- 기존 vault_id 처리 방식
- 기존 검색 로그 저장 방식
- 기존 embedding / chunking / indexing 흐름
- 기존 파일 변경 감지 또는 sync 관련 코드 존재 여부
- 테스트 구조
- 실행 방법
- 환경변수 관리 방식

분석 결과를 바탕으로 기존 스타일과 아키텍처를 최대한 유지해라.

무리하게 구조를 갈아엎지 말고, 필요한 범위에서 점진적으로 확장해라.

## 2. Obsidian Local REST API 플러그인 조사

Obsidian Local REST API 플러그인을 조사해라.

확인할 것:

- Vault 파일 목록 조회 가능 여부
- 파일 읽기 가능 여부
- 파일 생성 가능 여부
- 파일 수정 가능 여부
- 파일 삭제 가능 여부
- active file 또는 Vault metadata 관련 API 존재 여부
- 인증 방식
- local endpoint 기본 주소
- HTTPS / 인증서 사용 여부
- MCP endpoint 제공 여부
- 외부에서 파일을 수정했을 때 Obsidian 앱이 해당 변경을 즉시 인식하는지 여부
- Vault 또는 문서 refresh/reload에 해당하는 API가 있는지 여부

중요:

- Local REST API 플러그인만으로 “외부 파일 변경 후 Obsidian 화면/Vault 새로고침”이 가능한지 반드시 확인해라.
- 가능하다면 sync-agent가 파일을 쓰고 난 후 Local REST API를 호출하여 Obsidian이 변경을 인식하도록 구현해라.
- Local REST API에 명확한 refresh/reload API가 없다면, Obsidian의 file watcher가 자연스럽게 인식하는지 검증해라.
- 그래도 안정적이지 않다면 Vault File Refresh 플러그인 또는 유사 플러그인을 선택 옵션으로 검토해라.
- 단, Obsidian 플러그인에 강하게 의존하지 말고, 기본 Sync 로직은 파일 시스템 + 자체 API 기준으로 동작해야 한다.

## 3. 구현 계획 작성

구현 전에 상세한 작업 계획을 작성해라.

계획에는 반드시 포함해라.

- DB schema 변경안
- API endpoint 설계
- sync-agent 설계
- conflict 감지 방식
- local manifest 설계
- soft delete 정책
- vectorizing queue 연동 방식
- conflict 파일 벡터화 제외 방식
- Obsidian Local REST API 연동 방식
- 테스트 전략
- 마이그레이션 전략
- 롤백 전략

계획에는 “기존 구현에서 재사용할 것”과 “새로 추가할 것”을 구분해서 적어라.

계획을 작성한 뒤, 바로 구현에 들어가라.

---

# 목표 아키텍처

목표 구조는 다음과 같다.

```text
PC A Obsidian Vault
   ↓↑
PC A sync-agent
   ↓↑
Sync API Server
   ↓
Canonical Vault Storage
   ↓
Vectorizing Queue / Worker
   ↓
Vector DB
   ↓
MCP Server
   ↓
Coding Agent / Agent
```

여러 PC가 존재할 수 있다.

```text
PC A sync-agent ─┐
PC B sync-agent ─┼── Sync API Server ── Canonical Vault
PC C sync-agent ─┘
```

서버의 Canonical Vault가 정본이다.

로컬 PC의 Vault는 서버와 동기화되는 working copy로 본다.

---

# 핵심 동기화 정책

## 1. Revision 기반 동기화

모든 Vault 파일은 revision을 가진다.

예시:

```text
Java/Spring/JPA.md revision 17
```

클라이언트가 파일을 수정해서 업로드할 때는 반드시 자신이 알고 있는 base_revision을 함께 보낸다.

```json
{
  "vault_id": "personal",
  "device_id": "macbook-pro",
  "path": "Java/Spring/JPA.md",
  "base_revision": 17,
  "content_hash": "sha256:...",
  "content": "..."
}
```

서버의 현재 revision이 base_revision과 같으면 정상 업데이트한다.

서버의 현재 revision이 base_revision과 다르면 409 Conflict를 반환한다.

여기서 “서버의 현재 revision”은 해당 파일의 현재 revision을 의미한다.
sync cursor와 sync_event revision은 vault 단위 전역 revision을 사용한다.

```text
client base_revision = 17
server current_revision = 19

=> conflict
=> 자동 병합 금지
=> 409 Conflict 반환
```

## 2. 자동 병합 금지

자동 merge는 구현하지 않는다.

Conflict 발생 시 서버는 다음 정보를 반환한다.

```json
{
  "error": "SYNC_CONFLICT",
  "vault_id": "personal",
  "path": "Java/Spring/JPA.md",
  "client_base_revision": 17,
  "server_revision": 19,
  "server_content_hash": "sha256:...",
  "message": "The file was modified by another device. Manual resolution is required."
}
```

sync-agent는 이 응답을 받으면 로컬에 conflict 파일을 생성한다.

예시:

```text
Java/Spring/JPA.conflict.macbook-pro.20260707-121500.md
```

Conflict 파일에는 최소한 다음 내용을 포함해라.

```markdown
# Sync Conflict

- Path: Java/Spring/JPA.md
- Device: macbook-pro
- Client base revision: 17
- Server revision: 19
- Created at: 2026-07-07T12:15:00+09:00

## Local Version

...

## Server Version

...
```

Conflict 파일은 반드시 벡터화 대상에서 제외한다.

제외 패턴 예시:

```text
*.conflict.*.md
*.sync-conflict*.md
```

## 3. Soft Delete

삭제는 즉시 hard delete 하지 않는다.

DELETE 요청이 오면 서버는 다음처럼 처리한다.

```text
vault_file.deleted = true
vault_file.deleted_at = now()
revision 증가
sync_event 생성
vector index 비활성화
```

실제 파일 삭제는 일정 기간 이후 별도 cleanup job에서 처리한다.

기본 보존 기간은 7일 또는 설정값으로 둔다.

복구 API도 설계한다.

```http
POST /api/vaults/{vault_id}/files/{path}/restore?revision={revision}
```

기존 archive API 또는 archived table이 있으면, 이를 사용자가 요청한 삭제의 즉시 처리 경로로 쓰지 말고 soft delete 이후 보존/cleanup 구현에 재사용할 수 있는지 검토해라.

## 4. Hash 기반 변경 감지

mtime만 믿지 마라.

로컬 sync-agent는 다음 값을 함께 사용해 변경 여부를 판단한다.

```text
path
size
mtime
sha256
server_revision
```

최종 변경 여부는 sha256으로 판단한다.

## 5. Local Manifest

각 PC의 sync-agent는 로컬 manifest 파일을 관리해야 한다.

예시:

```json
{
  "vault_id": "personal",
  "device_id": "macbook-pro",
  "last_sync_cursor": "0000000000000017",
  "files": {
    "Java/Spring/JPA.md": {
      "server_revision": 17,
      "content_hash": "sha256:...",
      "last_synced_at": "2026-07-07T12:00:00+09:00"
    }
  }
}
```

manifest 파일은 Vault 내부에 둘 경우 Obsidian 문서로 인식되지 않도록 숨김 디렉토리에 저장한다.

추천 위치:

```text
.obsidian-sync-agent/manifest.json
```

단, 이 디렉토리는 Obsidian 문서 인덱싱과 Vectorizing 대상에서 제외한다.

---

# DB Schema 설계

기존 DB 구조를 먼저 확인하고, 필요한 경우 migration을 작성해라.

최소한 다음 개념이 필요하다.

## vault_file

```text
vault_id
path
revision
content_hash
size
deleted
deleted_at
updated_at
updated_by_device_id
```

기존 `vault_files` 테이블이 있다면 이 개념은 해당 테이블 확장을 우선 검토한다.

## vault_file_version

```text
vault_id
path
revision
content_hash
content
size
created_at
created_by_device_id
```

## sync_device

```text
vault_id
device_id
device_name
last_seen_at
created_at
```

## sync_event

```text
vault_id
revision
path
event_type
content_hash
deleted
created_at
created_by_device_id
```

event_type 예시:

```text
CREATE
UPDATE
DELETE
RESTORE
```

## sync_conflict

```text
vault_id
path
server_revision
client_base_revision
device_id
status
created_at
resolved_at
```

status 예시:

```text
OPEN
RESOLVED
IGNORED
```

중요:

- vault_id + path는 파일의 논리적 식별자다.
- revision은 vault 단위의 전역 revision으로 관리하는 것을 우선 적용해라.
- 파일별 현재 revision은 해당 파일의 마지막 sync_event revision을 참조하는 값으로 둔다.
- 별도 file_revision을 추가할 수도 있지만, conflict 판정이 vault 전체 변경 때문에 과도하게 발생하지 않도록 해야 한다.
- sync cursor는 vault 전역 revision 기반으로 구현한다.
- 파일 내용은 DB에 저장할 수도 있고, 파일 시스템/object storage에 저장할 수도 있다.
- 개인용이면 우선 DB 저장 또는 서버 파일 시스템 저장 중 기존 프로젝트와 맞는 방식을 선택해라.
- 단, version history는 반드시 남겨라.

---

# API Endpoint 설계

기존 API 스타일을 따르되, 최소한 다음 API를 구현해라. Endpoint는 조금 달라도 상관없다.

## 1. Device 등록

```http
POST /api/vaults/{vault_id}/sync/devices
```

Request:

```json
{
  "device_id": "macbook-pro",
  "device_name": "MacBook Pro"
}
```

Response:

```json
{
  "vault_id": "personal",
  "device_id": "macbook-pro",
  "registered": true
}
```

## 2. 변경분 조회

```http
GET /api/vaults/{vault_id}/sync/changes?since={cursor}&device_id={device_id}
```

Response:

```json
{
  "vault_id": "personal",
  "from_cursor": "17",
  "to_cursor": "21",
  "changes": [
    {
      "revision": 18,
      "path": "Java/Spring/JPA.md",
      "event_type": "UPDATE",
      "content_hash": "sha256:...",
      "deleted": false,
      "updated_at": "2026-07-07T12:00:00+09:00"
    }
  ]
}
```

## 3. 파일 다운로드

```http
GET /api/vaults/{vault_id}/files/{path}
```

Response:

```json
{
  "vault_id": "personal",
  "path": "Java/Spring/JPA.md",
  "revision": 18,
  "content_hash": "sha256:...",
  "content": "..."
}
```

path는 slash가 포함되므로 URL encoding을 정확히 처리해라.
FastAPI에서 구현할 때는 `/{path:path}`를 쓰거나 path를 query/body로 받는 대안을 선택하고, 선택한 방식을 테스트에 포함해라.

## 4. 파일 업로드

```http
PUT /api/vaults/{vault_id}/files/{path}
```

Request:

```json
{
  "device_id": "macbook-pro",
  "base_revision": 17,
  "content_hash": "sha256:...",
  "content": "..."
}
```

신규 파일 생성 요청은 `base_revision: 0`을 사용한다.

성공 Response:

```json
{
  "vault_id": "personal",
  "path": "Java/Spring/JPA.md",
  "revision": 18,
  "content_hash": "sha256:..."
}
```

Conflict Response:

```http
409 Conflict
```

```json
{
  "error": "SYNC_CONFLICT",
  "vault_id": "personal",
  "path": "Java/Spring/JPA.md",
  "client_base_revision": 17,
  "server_revision": 19,
  "server_content_hash": "sha256:..."
}
```

## 5. 파일 삭제

```http
DELETE /api/vaults/{vault_id}/files/{path}
```

Request:

```json
{
  "device_id": "macbook-pro",
  "base_revision": 17
}
```

서버 revision이 다르면 409 Conflict를 반환해라.

## 6. 파일 복구

```http
POST /api/vaults/{vault_id}/files/{path}/restore
```

Request:

```json
{
  "device_id": "macbook-pro",
  "restore_revision": 15
}
```

## 7. Sync 상태 확인

```http
GET /api/vaults/{vault_id}/sync/status?device_id={device_id}
```

Response 예시:

```json
{
  "vault_id": "personal",
  "device_id": "macbook-pro",
  "server_revision": 21,
  "device_last_seen_revision": 18,
  "pending_changes": 3,
  "open_conflicts": 1
}
```

---

# sync-agent 설계

sync-agent는 각 PC에서 실행되는 로컬 프로그램이다.

기존 프로젝트에 CLI 구조가 있다면 CLI로 구현하고, 없다면 별도 패키지 또는 실행 스크립트로 구현해라.

sync-agent 기능:

```text
1. Vault 폴더 스캔
2. manifest 로드
3. 로컬 변경 파일 탐지
4. 서버 변경분 pull
5. 로컬 변경분 push
6. conflict 처리
7. manifest 갱신
8. Obsidian Local REST API refresh 연동
```

## 동기화 순서

추천 순서:

```text
1. device 등록
2. 서버 변경분 pull
3. 로컬에 서버 변경사항 적용
4. 로컬 변경분 scan
5. 로컬 변경분 push
6. conflict 있으면 conflict 파일 생성
7. manifest 갱신
8. Obsidian refresh 시도
```

pull을 먼저 하는 이유:

- 서버의 최신 revision을 먼저 반영해야 push conflict를 줄일 수 있다.

단, 로컬 파일이 이미 수정되어 있고 서버에도 같은 파일 변경이 있으면 conflict로 처리해야 한다.
서버에서 받은 변경분을 로컬에 적용하기 전에 같은 path의 로컬 hash가 manifest의 마지막 hash와 다른지 확인해라. 다르면 서버 변경을 덮어쓰지 말고 conflict 파일을 생성한다.

## 로컬 변경 탐지

운영체제 임시 파일들과 다음 파일은 무시해라.

```text
.obsidian/workspace*
.obsidian/cache/**
.obsidian-sync-agent/**
.trash/**
.DS_Store
Thumbs.db
*.conflict.*.md
*.sync-conflict*.md
```

Markdown 파일뿐 아니라 이미지/PDF 첨부파일도 Vault에 있을 수 있으므로, v1에서 지원 범위를 명확히 하라.

v1 권장:

```text
- .md 파일 우선 지원
- 이미지/PDF/첨부파일은 metadata만 감지하거나, v2 과제로 분리
```

기존 시스템이 첨부파일을 이미 처리한다면 기존 방식에 맞춰라.
서버 API가 v1에서 `.md`만 원문 sync 대상으로 지원한다면, 첨부파일은 무시/metadata-only/명시적 에러 중 하나로 일관되게 처리하고 문서화해라.

---

# Obsidian Local REST API 연동 요구사항

sync-agent는 Obsidian Local REST API 플러그인이 설정되어 있으면 선택적으로 연동해야 한다.

환경변수 또는 config 예시:

```yaml
obsidian:
  local_rest_api_enabled: true
  base_url: 'https://127.0.0.1:27124'
  api_key: '${OBSIDIAN_LOCAL_REST_API_KEY}'
  verify_tls: false
```

구현 전 반드시 실제 지원 endpoint를 확인해라.

가능하면 다음 기능을 구현해라.

## 1. 연결 확인

```text
- Local REST API plugin이 실행 중인지 확인
- 인증 성공 여부 확인
- 실패해도 sync 자체는 실패시키지 말고 warning으로 남김
```

## 2. 파일 갱신 후 Obsidian 반영

sync-agent가 서버 변경분을 로컬 Vault에 적용한 뒤, Obsidian이 변경을 인식하도록 가능한 방법을 시도해라.

가능한 선택지:

```text
A. Local REST API에 refresh/reload에 해당하는 endpoint가 있으면 호출
B. 특정 파일 metadata/read endpoint를 호출하여 Obsidian 내부 인식을 유도할 수 있는지 검증
C. Local REST API만으로 불가능하면 Vault File Refresh 플러그인 사용 가능성을 문서화
D. 그래도 불가능하면 OS file watcher에 의존하고, 사용자가 Obsidian에서 확인하도록 안내
```

중요:

- 지원되지 않는 endpoint를 임의로 가정하지 마라.
- 반드시 문서 또는 실제 OpenAPI spec을 확인해라.
- 구현 가능한 경우에만 코드로 반영해라.
- 구현이 불가능하면 명확히 TODO와 문서로 남겨라.

## 3. Obsidian 앱 미실행 시 동작

Obsidian 앱이 실행 중이 아니어도 sync-agent는 정상 동작해야 한다.

Local REST API 연동 실패가 전체 sync 실패로 이어지면 안 된다.

```text
Sync 성공
Obsidian refresh 실패
=> warning log
=> exit code는 sync 성공으로 유지
```

단, 사용자가 `--require-obsidian-refresh` 옵션을 주면 refresh 실패 시 실패 처리할 수 있게 해라.

---

# Vectorizing 연동

서버에서 파일 commit이 성공한 뒤에만 vectorizing queue에 등록해라.

흐름:

```text
PUT file 성공
→ vault_file 업데이트
→ vault_file_version 저장
→ sync_event 생성
→ vectorizing_job 생성
→ worker가 Markdown parsing
→ chunk 생성
→ embedding 생성
→ Vector DB upsert
```

삭제 시:

```text
DELETE file 성공
→ soft delete
→ sync_event 생성
→ vector index 비활성화
```

복구 시:

```text
RESTORE 성공
→ 새 revision 생성
→ vectorizing_job 생성
```

Conflict 파일은 vectorizing 대상에서 제외해라.

또한 다음 파일/폴더도 제외해라.

```text
.obsidian/**
.obsidian-sync-agent/**
.trash/**
*.conflict.*.md
*.sync-conflict*.md
```

현재 프로젝트가 즉시 reindex API 중심이고 별도 queue가 없다면, 구현 계획에서 “최소 queue 테이블을 추가할지” 또는 “commit 이후 pending 상태를 만들고 기존 reindex worker/service가 처리하게 할지”를 선택해라. 어떤 경우에도 commit 실패 또는 conflict 발생 시 vectorizing을 시작하지 않는다.

---

# MCP Server 요구사항

기존 MCP Server가 있다면 깨지지 않게 유지하라.

다만 sync 구조가 추가되면서 다음 정보를 활용할 수 있게 개선해라.

## search_notes

입력:

```json
{
  "vault_id": "personal",
  "query": "JPA optimistic lock error",
  "limit": 5
}
```

반환값에는 다음 정보를 포함해라.

```json
{
  "vault_id": "personal",
  "path": "Java/Spring/JPA.md",
  "title": "JPA 오류 정리",
  "heading_path": ["JPA", "Optimistic Lock"],
  "chunk": "...",
  "score": 0.87,
  "revision": 18,
  "updated_at": "2026-07-07T12:00:00+09:00"
}
```

## get_note

특정 path의 최신 원문을 조회한다.

```json
{
  "vault_id": "personal",
  "path": "Java/Spring/JPA.md"
}
```

반환값에는 revision과 content_hash를 포함해라.

## get_sync_status

Agent가 필요할 경우 Vault 최신성 상태를 확인할 수 있게 한다.

```json
{
  "vault_id": "personal"
}
```

반환값:

```json
{
  "server_revision": 21,
  "open_conflicts": 1,
  "pending_vectorizing_jobs": 0
}
```

---

# 보안 요구사항

개인용 서비스지만 최소 보안은 유지해라.

- 모든 Sync API는 Bearer Token 인증 필요
- token은 로그에 남기지 말 것
- path traversal 방지 필수
- `../` 경로 차단
- absolute path 입력 차단
- vault root 밖으로 파일 쓰기 금지
- 허용된 vault_id만 접근 가능
- request size limit 설정
- Markdown content log는 기본적으로 저장하지 말 것
- 검색 로그는 저장하되 민감 정보가 과도하게 남지 않도록 주의
- Obsidian Local REST API key는 환경변수 또는 local config로만 관리

---

# 테스트 요구사항

구현 후 반드시 테스트를 작성하고 실행해라.

## Unit Test

필수 테스트:

- sha256 계산
- ignore pattern
- path normalization
- path traversal 차단
- local manifest load/save
- revision 비교
- conflict 판정
- soft delete 판정
- vectorizing 대상 제외 판정

## API Test

필수 테스트:

- device 등록
- 파일 최초 업로드
- 파일 업데이트
- base_revision 일치 시 성공
- base_revision 불일치 시 409 Conflict
- 삭제 성공
- 삭제 conflict
- restore 성공
- changes API cursor 동작
- status API 동작
- 인증 실패

## Sync Agent Test

필수 시나리오:

```text
1. 빈 로컬 Vault가 서버 변경분을 pull한다.
2. 로컬에서 새 md 파일을 만들고 push한다.
3. PC A와 PC B가 같은 파일을 수정하면 한쪽은 409 Conflict가 발생한다.
4. Conflict 파일이 로컬에 생성된다.
5. Conflict 파일은 다음 sync에서 업로드/벡터화되지 않는다.
6. 삭제는 soft delete로 처리된다.
7. 서버에서 삭제된 파일이 로컬에서도 삭제 또는 tombstone 처리된다.
8. Obsidian Local REST API가 꺼져 있어도 sync는 성공한다.
9. Obsidian Local REST API가 켜져 있으면 refresh 연동을 시도한다.
```

## Integration Test

가능하면 임시 Vault 디렉토리 2개를 만들어 다중 PC 상황을 재현해라.

```text
tmp/vault-pc-a
tmp/vault-pc-b
```

시나리오:

```text
1. PC A가 JPA.md 생성 후 sync
2. PC B가 pull
3. PC A와 PC B가 동시에 JPA.md 수정
4. PC A sync 성공
5. PC B sync 시 409 Conflict
6. PC B에 conflict 파일 생성
7. 서버에는 PC A 버전만 latest로 유지
8. vectorizing queue에는 conflict 파일이 들어가지 않음
```

---

# 문서화 요구사항

구현 후 다음 문서를 작성하거나 업데이트해라.

## docs/sync-api.md

포함 내용:

- 전체 구조
- API 목록
- revision 정책
- conflict 정책
- soft delete 정책
- restore 방법
- 인증 방법
- 예시 curl

## docs/sync-agent.md

포함 내용:

- 설치 방법
- 설정 파일 예시
- 실행 방법
- manifest 위치
- ignore pattern
- Obsidian Local REST API 연동 방법
- Obsidian refresh가 실패했을 때의 동작
- conflict 해결 방법

## docs/vectorizing.md

포함 내용:

- sync commit 이후 vectorizing 흐름
- 제외 파일 패턴
- 재색인 방법
- 삭제/복구 시 vector index 처리

---

# 구현 시 주의사항

- 기존 기능을 깨지 마라.
- 기존 MCP Tool의 입력/출력을 변경해야 한다면 backward compatibility를 고려해라.
- 대규모 리팩토링보다 작은 단위의 변경을 선호해라.
- DB migration은 되돌릴 수 있게 작성해라.
- 오류 코드는 명확하게 정의해라.
- conflict는 정상적인 비즈니스 흐름이므로 500으로 처리하지 마라.
- 파일 경로 처리는 반드시 테스트해라.
- Obsidian Local REST API 관련 기능은 optional integration으로 구현해라.
- Local REST API plugin이 없어도 sync는 동작해야 한다.
- Vector DB는 원본이 아니므로 언제든 재생성 가능해야 한다.
- 원본 Markdown과 version history 보존을 최우선으로 해라.

---

# 최종 완료 조건

작업이 완료되려면 다음 조건을 만족해야 한다.

- 자동 병합 없는 API Sync가 동작한다.
- 여러 PC/device를 구분할 수 있다.
- 파일별 revision 또는 vault 전역 revision이 관리된다.
- base_revision 불일치 시 409 Conflict가 발생한다.
- Conflict 파일이 로컬에 생성된다.
- Conflict 파일은 sync 대상과 vectorizing 대상에서 제외된다.
- 삭제는 soft delete로 처리된다.
- version history가 남는다.
- commit 성공 후 vectorizing job이 생성된다.
- MCP 검색 결과가 최신 revision 기준으로 동작한다.
- Obsidian Local REST API 연동 가능성을 검증했다.
- 가능하면 Local REST API를 통해 Obsidian refresh/reload 또는 변경 인식 유도 로직을 구현했다.
- 불가능하면 Vault File Refresh 플러그인 등 대안을 문서화했다.
- 테스트가 통과한다.
- docs/sync-api.md, docs/sync-agent.md, docs/vectorizing.md가 업데이트되어 있다.

---

# 산출물

최종 응답에는 다음을 포함해라.

1. 변경 요약
2. 설계 요약
3. 추가/수정된 파일 목록
4. DB migration 요약
5. API 목록
6. sync-agent 사용 방법
7. Obsidian Local REST API 연동 결과
8. 테스트 실행 결과
9. 남은 TODO
10. 주의해야 할 운영 포인트
