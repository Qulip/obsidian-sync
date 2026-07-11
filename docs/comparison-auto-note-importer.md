# 기능 비교 분석: obsidian-sync vs obsidian-auto-note-importer

> 작성일: 2026-07-11
> 분석 방법: 두 저장소를 각각 서브 에이전트로 상세 분석 후 종합 비교.
> 비교 대상: `/Users/ryuilkwon/Github/obsidian-auto-note-importer` (v1.0.2, Obsidian 커뮤니티 플러그인)

---

## 1. 두 프로젝트의 성격

| 구분 | obsidian-sync (본 프로젝트) | obsidian-auto-note-importer |
|---|---|---|
| 형태 | FastAPI 서버 + CLI 동기화 에이전트 + MCP 서버 | Obsidian 플러그인 (TypeScript, 데스크톱/모바일) |
| 동기화 대상 | 로컬 vault ↔ 자체 서버(PostgreSQL) | vault ↔ 외부 DB (Airtable / SeaTable / Supabase) |
| 동기화 단위 | 파일 전체 (.md, 해시 기반) | 레코드/필드 단위 (frontmatter 매핑) |
| 동기화 방향 | 양방향 (리비전 기반) + 단방향 업로드 경로 | 양방향 (pull/push + 2-phase bidirectional) |
| 부가 기능 | pgvector 임베딩 검색, 버전 히스토리, MCP 도구 | 템플릿, Obsidian Bases 연동, 멀티 컨피그 |

두 프로젝트는 목적이 다르지만(파일 동기화 vs 레코드 동기화) **양방향 동기화의 핵심 문제 — 변경 감지, 충돌 처리, 자기 루프 방지, 재시도 — 를 공유**하므로 설계 아이디어의 상호 참조 가치가 크다.

---

## 2. obsidian-sync 현재 기능 요약

### 아키텍처
저장소 내에 두 개의 병렬 동기화 경로가 공존한다.

- **경로 A (양방향, 리비전 기반)**: `api/routes/sync.py` + `services/revision_sync.py` + `repositories/sync.py`. `obsidian-sync-agent` CLI가 사용. 낙관적 동시성(`base_revision`), 이벤트 로그, 전문 버전 히스토리, `SELECT ... FOR UPDATE` 행 잠금.
- **경로 B (단방향 해시 매니페스트 업로드)**: `api/routes/vaults.py` + `services/vault_sync.py`. `scripts/upload_obsidian.py`와 MCP 도구(`sync_manifest`/`sync_file`)가 사용. 리비전 개념 없음.

두 경로가 같은 `vault_files` 테이블을 공유하되 필드 사용법이 다르다.

### 동기화 메커니즘 (경로 A)
- **변경 감지**: SHA-256 해시 비교만 사용(`sync_agent/scanner.py:53-58`). mtime은 기록만 하고 비교에 미사용. 파일 감시 데몬 없음 — 순수 폴링, cron/launchd로 사용자가 직접 스케줄링.
- **충돌 감지**: 서버가 `base_revision != row.revision`이면 409 SYNC_CONFLICT(`services/revision_sync.py:386-462`). 실제 uvicorn 서브프로세스로 race를 재현하는 동시성 통합 테스트 보유(`tests/test_sync_concurrency.py`).
- **충돌 해소**: 자동 병합 없음 — 항상 `.conflict.{device_id}.{timestamp}.md` 파일 생성 후 수동 해결(`sync_agent/conflict.py:15-73`).
- **삭제**: soft delete + `restore_file`로 임의 리비전 복구. `scripts/cleanup_deleted_files.py`가 보존기간 경과분을 아카이브(수동 cron).

### 강점 (비교 대상 대비 앞서 있는 부분)
- 리비전 기반 낙관적 동시성 + 서버측 행 잠금 — 비교 대상의 "push 직전 재조회 diff"보다 정합성이 강함.
- 파일 전문 버전 히스토리(`vault_file_versions`)와 리비전 단위 복구.
- Soft delete + 보존기간 기반 아카이브.
- 토큰 SHA-256 해시 저장, 상수시간 비교, 요청 크기 제한 미들웨어.
- 실제 서브프로세스 기반 동시성 통합 테스트.
- pgvector 임베딩 검색 등 지식 서비스 레이어.

### 확인된 한계
1. 첨부파일 동기화 미지원 (서버 API는 이미지·PDF 지원, 에이전트는 `.md`만 — `docs/sync-agent.md:3`).
2. 실시간 파일 감시 없음 — 순수 폴링 + 수동 스케줄링.
3. 클라이언트 재시도 로직 부재 — 실패 시 다음 스케줄 실행에 의존.
4. 경로 A/B 이중 구조 — 특히 MCP의 `force_sync_file`(`services/vault_sync.py:176-228`)은 base_revision 검사 없이 무조건 덮어써 리비전 체계를 조용히 우회할 수 있음.
5. 버전 히스토리 무제한 누적 (리텐션/압축 정책 없음).
6. 자체 구현 YAML 서브셋 프론트매터 파서(`domain/frontmatter.py`) + 전용 테스트 부재. 검증은 경로 B에만 적용되고 경로 A는 프론트매터를 검사하지 않음.
7. 프론트매터 파서, 청킹/임베딩, MCP 도구, `VaultSyncService`(경로 B)에 전용 단위 테스트 없음.
8. DB 백업이 백업 스크립트 범위 밖(파일시스템만).

---

## 3. obsidian-auto-note-importer 기능 요약

### 아키텍처
- Provider-agnostic core + pluggable provider: `DatabaseProvider` 인터페이스 + 3중 레지스트리(provider factory / field mapper / credential form — `src/services/provider-registry.ts`).
- 멀티 컨피그: `ConfigManager`가 여러 `ConfigInstance`를 관리, 각 인스턴스가 자신만의 provider·FileWatcher·SyncOrchestrator·SyncQueue·ConflictResolver·스케줄러를 소유.
- 테스트가 소스보다 1.6배 많음: vitest 단위 테스트 약 773케이스 + 실제 Obsidian 앱을 CDP로 조작하는 E2E 스위트.

### 동기화 메커니즘
- **레코드 식별**: frontmatter의 `primaryField`(remote record id)를 provider-agnostic 불변 매칭 키로 사용.
- **양방향**: push → `formulaSyncDelay`(기본 1500ms) 대기(서버 계산 필드 반영) → pull 하는 2-phase 설계.
- **변경 감지**: vault `modify` 이벤트 구독 + debounce + 큐잉. `externalSyncing`/`internalSyncing` 이원화 플래그로 **자기 쓰기 재감지 무한 루프를 구조적으로 차단**(`src/file-operations/file-watcher.ts:51-105`).
- **충돌**: push 직전 원격 재조회 후 필드 단위 diff. 해결 모드 3종 — `obsidian-wins` / `remote-wins`(충돌 필드만 제외하고 push) / `manual`(중단+알림).
- **중복 방지**: 재귀 폴더 스캔으로 기존 `primaryField` 집합 구성, diff 기반 불필요 쓰기 방지.

### 안전성 설계
- **Fail-closed 필드 타입 매핑**: 알 수 없는 타입은 무조건 읽기 전용/미푸시(`src/services/airtable-field-mapper.ts:137-150`). 파일명/서브폴더는 provider별 안전 타입 화이트리스트만 허용.
- **Sanitizer 다층 방어**(`src/utils/sanitizers.ts`): 금지문자 치환, 세그먼트별 `.`/`..` 제거, **Windows 예약어(CON, PRN, AUX, NUL, COM1-9, LPT1-9) 거부**, 255자 제한. 과거 이슈(#96)를 계기로 같은 검증을 두 계층에서 중복 수행.
- **스키마 인지형 write 게이팅**: Supabase provider가 PostgREST OpenAPI 스펙에서 컬럼별 read-only 여부를 조회해 push 페이로드에서 제외, 쓰기 가능 컬럼이 0개면 사전 경고. Secret 키 감지 시 경고, RLS 환경용 셋업 마법사(fail-closed 검증).

### 에러 처리·재시도
- **RateLimiter**(`src/services/rate-limiter.ts`): 크레덴셜 단위 공유, 최소 요청 간격 강제, 429의 `Retry-After` 파싱, **일시적 네트워크 오류 화이트리스트**(`ECONNRESET` 등 알려진 패턴만) + 지수 백오프, 알 수 없는 에러는 즉시 실패(fail fast). 408/429/5xx=transient, 4xx=permanent.
- **배치 부분 실패 처리**: 배치 실패를 개별 레코드 실패로 변환해 전체 동기화가 막히지 않게 함.
- **debugMode**: 모든 지연시간을 5배로 늘려 타이밍 이슈를 눈으로 관찰 가능하게 하는 장치.

### 한계
- 첨부파일은 실제 다운로드 없이 URL 참조만 지원.
- 자격증명은 plugin data.json에 평문 저장.
- Notion/Custom API는 타입만 존재(미구현).

---

## 4. 기능별 상세 비교

| 기능 영역 | obsidian-sync | auto-note-importer | 우위 |
|---|---|---|---|
| 충돌 **감지** | 리비전 기반 낙관적 동시성 + 행 잠금 | push 직전 재조회 + 필드 diff | **obsidian-sync** (정합성) |
| 충돌 **해소** | conflict 파일 생성(수동)만 | 정책 3종 선택 가능 | **importer** (유연성) |
| 변경 감지 | 폴링 + SHA-256 해시 | 파일 이벤트 + debounce + 자기루프 방지 | **importer** (즉시성) |
| 버전 히스토리 | 전문 버전 저장 + 리비전 복구 | 없음 | **obsidian-sync** |
| 삭제 처리 | soft delete + 보존기간 아카이브 | 해당 없음(중복 스킵만) | **obsidian-sync** |
| 재시도/rate limit | 없음 (다음 스케줄 의존) | 화이트리스트 재시도 + 지수백오프 + Retry-After | **importer** |
| 경로/파일명 안전성 | traversal·절대경로·NUL 차단 (`domain/paths.py`) | + Windows 예약어, 다층 방어, 255자 제한 | **importer** (커버리지) |
| 프론트매터 처리 | 자체 YAML 서브셋 파서, 경로 B에만 적용 | 파싱·직렬화·Bases 호환 포맷팅 전반 | **importer** |
| 첨부파일 | 서버는 지원, 에이전트 미지원 | URL 참조만 (다운로드 없음) | 둘 다 미완 — **차별화 기회** |
| 인증/보안 | 토큰 해시 저장, 상수시간 비교 | 평문 저장, 단 키 위험등급 자동 감지 | **obsidian-sync** (저장) / importer (UX) |
| 테스트 | 경로 A·동시성은 두터움, 경로 B·파서·MCP 공백 | 소스 대비 1.6배, mapper parity, CDP E2E | **importer** (전면성) |
| 동시 요청 제어 | 서버측 행 잠금 | 클라이언트측 SyncQueue(큐잉+dedup) | 상호 보완적 |
| 확장성 | 단일 백엔드 전제 | provider 레지스트리 패턴 | **importer** |

---

## 5. 개선 제안 (우선순위순)

### P1 — 정합성/안전성 직결

1. **`force_sync_file`의 리비전 우회 차단** (`services/vault_sync.py:176-228`)
   MCP 경로가 base_revision 검사 없이 덮어쓰므로, 로컬 에이전트와 AI 에이전트가 같은 파일을 동시에 건드리면 경로 A의 리비전 체계가 조용히 무력화된다. importer의 **fail-closed 게이팅** 원칙 적용: MCP 쓰기도 리비전 검사를 기본으로 통과시키고, 우회는 명시적 opt-in 플래그 + 이벤트 로그 기록으로 제한. 장기적으로 경로 B를 경로 A 위에 재구현해 이중 구조 해소.

2. **클라이언트 재시도 + 백오프 도입** (`sync_agent/client.py`)
   importer의 RateLimiter 설계를 이식: 일시적 네트워크 오류 **화이트리스트**(ECONNRESET, ETIMEDOUT 등)만 지수 백오프 재시도, 4xx는 즉시 실패, 429/503의 `Retry-After` 존중. 현재는 일시적 오류 한 번에 전체 동기화가 실패하고 다음 cron까지 방치된다.

3. **경로 sanitization 보강** (`domain/paths.py`)
   traversal 차단은 이미 잘 되어 있으나, importer가 커버하는 **Windows 예약 파일명(CON, PRN, AUX, NUL, COM1-9, LPT1-9)**, 파일명 255자 제한, 결과가 빈 문자열/전부 `-`인 경우의 거부가 없다. 크로스 플랫폼 vault를 고려하면 추가 가치가 있고, importer처럼 "한 계층 검증으로 충분하다고 가정하지 않는" 다층 방어를 명시한다.

### P2 — 기능 격차 해소

4. **충돌 해결 정책 옵션화** (`sync_agent/conflict.py`)
   현재는 항상 conflict 파일 생성(= importer의 `manual`에 해당)뿐이다. importer처럼 `local-wins` / `remote-wins` / `manual(conflict 파일)` 3모드를 에이전트 설정으로 제공하면, 단일 사용자·다중 기기 시나리오에서 대부분의 충돌을 자동 처리할 수 있다. 버전 히스토리가 이미 있으므로 자동 해소해도 복구 가능하다는 것이 importer 대비 유리한 점.

5. **첨부파일 동기화** — 최대 차별화 기회
   서버(`domain/files.py`)는 이미 이미지·PDF를 지원하므로 에이전트만 확장하면 된다. importer조차 URL 참조만 지원하는 영역이라, 실제 바이너리 양방향 동기화는 두 프로젝트 모두의 공백이다. 바이너리는 버전 전문 저장 대신 해시 기반 dedup 저장 고려.

6. **파일 이벤트 감시 모드 (watch 데몬)** (`sync_agent/`)
   폴링 유지 + 선택적 watch 모드(watchdog 라이브러리) 추가. importer에서 반드시 가져올 두 가지: **debounce 일괄 처리**와 **자기 쓰기 재감지 방지 플래그 이원화**(외부 동기화 중 vs 내부 쓰기 중). pull이 로컬 파일을 쓸 때 watcher가 이를 다시 push하는 루프는 watch 모드의 대표적 함정이다.

### P3 — 품질/운영

7. **테스트 공백 보강**
   경로 B(`VaultSyncService`), 프론트매터 파서, MCP 도구에 전용 테스트가 없다. importer의 **mapper parity 테스트**(구현체들이 인터페이스 계약을 동일하게 지키는지 교차 검증) 아이디어를 경로 A/B가 공유하는 `vault_files` 시맨틱 검증에 적용할 수 있다. 소스 파일 상단에 `@tested tests/...` 태그로 코드↔테스트 링크를 남기는 컨벤션도 저비용 고효율.

8. **버전 히스토리 리텐션 정책** (`vault_file_versions`)
   현재 무한 누적. "파일당 최근 N개 + 기간 기반" 정리를 `cleanup_deleted_files.py`류의 유지보수 스크립트로 추가.

9. **프론트매터 파서 정비** (`domain/frontmatter.py`)
   자체 YAML 서브셋 파서는 테스트도 없다. PyYAML(safe_load) 채택 또는 최소한 전용 테스트 추가. 검증을 경로 A에도 적용할지 여부를 명시적으로 결정(현재는 경로 B만 검증).

10. **운영 편의 소품**
    - importer의 **debugMode 지연 배율**: 에이전트에 디버그 모드를 두고 타이밍 관련 이슈 관찰을 쉽게.
    - 배치 push의 **부분 실패 허용**: 한 파일 실패가 전체 sync 실패(종료코드 2)로 번지지 않도록 파일 단위 결과 집계.
    - DB 백업을 백업 스크립트 범위에 포함 (`scripts/backup_vaults.py`는 현재 파일시스템만).

### 별도 검토 — Obsidian 플러그인 클라이언트

auto-note-importer가 Obsidian 플러그인으로 설치되는 것은 기능이 아니라 **클라이언트 배포 형태**의 차이다. obsidian-sync의 리비전 API(경로 A)는 클라이언트 중립적이므로, CLI 에이전트를 대체하지 않고 **두 번째 클라이언트로 플러그인을 추가**하는 선택지가 성립하며, 이는 위 개선 과제 여러 개를 한 번에 해결한다.

| 관점 | 효과 |
|---|---|
| 실시간 변경 감지 | `vault.on('modify')` 이벤트를 직접 수신 — watch 데몬(P2-6) 불필요, importer의 debounce·자기루프 방지 패턴 그대로 적용 가능. cron 설정도 불필요 |
| 모바일 동기화 | CLI는 iOS/Android 실행 불가. 플러그인(`isDesktopOnly: false`)이 휴대폰 vault를 커버하는 사실상 유일한 경로 |
| 미저장 편집 위험 | 현재 `reload_command:true`는 미저장 편집을 날릴 수 있어 기본 off. 플러그인은 에디터와 협조하는 vault API로 쓰므로 문제 자체가 소멸 |
| 설정/알림 UX | 설정 탭 UI, Notice 알림, 명령 팔레트 — importer의 UX 패턴 참고 가능 |

**비용**: 동기화 엔진의 TypeScript 재작성, Obsidian 실행 중에만 동작(헤드리스 정기 동기화는 여전히 CLI+cron 필요), 클라이언트 두 벌 유지보수.

**결론**: CLI 대체가 아닌 상호보완 구조(데스크톱 헤드리스 = CLI, 실시간·모바일 = 플러그인)로 검토. 우선순위는 P1 처리 후가 적절하나, 모바일 동기화가 중요하다면 P2 상단으로 승격할 가치가 있다.

### 도입하지 않아도 되는 것

- **provider 레지스트리 패턴**: importer는 3종 외부 DB를 추상화해야 했지만, obsidian-sync는 자체 서버 단일 백엔드라 현 시점에서는 과설계. 외부 소스(Notion 등) 수집 기능을 추가할 때 재검토.
- **push 직전 재조회식 충돌 감지**: 리비전 기반 방식이 이미 상위 호환.
- **템플릿/Bases 연동**: 레코드→노트 변환 도구의 기능이며 파일 동기화 서비스의 관심사가 아님.

---

## 6. 결론

obsidian-sync는 **동기화 코어의 정합성**(리비전, 행 잠금, 버전 히스토리, soft delete)에서 비교 대상보다 명확히 앞서 있다. 반면 auto-note-importer는 **클라이언트 견고성과 방어적 설계**(재시도/백오프, 충돌 정책 선택, 자기루프 방지, fail-closed 게이팅, sanitizer 다층 방어, 테스트 밀도)에서 배울 점이 많다.

최우선 과제는 새 기능이 아니라 **이미 잘 만든 리비전 체계를 우회하는 구멍(`force_sync_file`)을 막는 것**이고, 그다음이 클라이언트 재시도와 충돌 정책 옵션화다. 첨부파일 동기화는 두 프로젝트 모두의 공백이므로 가장 큰 차별화 기회다.
