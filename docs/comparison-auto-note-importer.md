# 기능 비교 분석: obsidian-sync vs obsidian-auto-note-importer

> 작성일: 2026-07-12
> 비교 대상: `/Users/ryuilkwon/Github/obsidian-auto-note-importer` v1.0.2
> 분석 방법: 두 저장소를 각각 서브 에이전트로 분리 분석한 뒤 종합했다. 사용자가 Sonnet 모델 사용을 요청했지만, 현재 서브 에이전트 도구가 Sonnet 모델로 수행했다. 본 프로젝트는 `.codegraph/` 인덱스가 있어 CodeGraph를 우선 사용했고, 비교 대상은 일반 파일 읽기/검색으로 확인했다.

---

## 요약 결론

`obsidian-sync`와 `obsidian-auto-note-importer`는 모두 Obsidian과 외부 상태를 양방향으로 맞추지만, 설계 목표가 다르다.

| 구분 | obsidian-sync | obsidian-auto-note-importer |
|---|---|---|
| 제품 형태 | FastAPI 서버 + Python CLI/watch agent + MCP/API | Obsidian 커뮤니티 플러그인 |
| 주된 목적 | 개인 vault를 자체 서버에 저장하고 revision 기반으로 여러 기기와 동기화, 이후 pgvector/MCP 검색에 활용 | Airtable/SeaTable/Supabase 레코드를 Obsidian note로 가져오고 frontmatter 변경을 원격 DB에 push |
| 동기화 단위 | 파일 전체, SHA-256 해시, vault 전역 revision | 원격 레코드/필드, frontmatter 매핑 |
| 정합성 모델 | 서버 권위 revision, 파일별 `base_revision`, 행 잠금, 이벤트 로그 | push 직전 원격 재조회 후 필드 diff |
| UX 표면 | CLI, watch daemon, Local REST API refresh, HTTP/MCP | Obsidian 설정 탭, 명령 팔레트, Notice, status bar, vault event watcher |
| 강점 | 정합성, 복구성, 자체 데이터 소유권, binary attachment sync, pgvector/MCP | Obsidian-native UX, 다중 설정, provider abstraction, 설정 검증, Bases/템플릿 |

현재 프로젝트는 동기화 코어의 정합성과 복구성에서 비교 대상보다 강하다. 반대로 비교 대상은 Obsidian 안에서 사용자가 직접 설정하고 상태를 확인하는 UX와 provider별 방어적 설정 검증이 훨씬 성숙하다.

가장 큰 개선 방향은 서버 코어를 플러그인으로 옮기는 것이 아니라, `obsidian-sync` 서버는 유지하고 **Obsidian 플러그인을 두 번째 클라이언트로 추가**하는 것이다. CLI/watch agent는 헤드리스·대용량·자동화에 계속 쓰고, 플러그인은 실시간 UX·모바일·설정 UI·충돌 확인을 담당하게 하는 방식이 현실적이다.

---

## 1. obsidian-sync 기능 분석

### 1.1 목적과 구조

본 프로젝트는 개인 Obsidian vault를 자체 FastAPI 서버에 저장하고 여러 로컬 기기와 동기화하는 서비스다. 서버는 파일 정본을 파일시스템에 두고, PostgreSQL에는 vault/file 메타데이터, revision 이벤트, 충돌, 버전 이력, 검색 청크, 토큰, 로그를 저장한다.

주요 흐름은 다음과 같다.

```text
local vault
  -> obsisync sync/status 또는 obsidian-sync-agent watch
  -> FastAPI Sync API
  -> vaults/<vault_id>/ canonical files + PostgreSQL metadata
  -> reindex/Ollama embedding
  -> knowledge_chunks(pgvector) + search API/MCP
```

레이어별 기능은 다음과 같다.

| 레이어 | 기능 |
|---|---|
| API | vault 관리, revision sync, 파일 조회/PUT/DELETE/RESTORE, reindex, semantic search, search log, MCP용 vault/search/note/status |
| CLI/agent | `obsisync sync`, `obsisync status`, Python `obsidian-sync-agent watch`; pull-scan-push-pull sync cycle; local manifest; conflict policy |
| DB | `vaults`, `vault_files`, `sync_devices`, `sync_events`, `sync_conflicts`, `vault_file_versions`, `knowledge_chunks`, `api_tokens`, search/index logs |
| services | `RevisionSyncService`가 양방향 sync 정합성 중심, `VaultSyncService`가 MCP one-way save의 fail-closed 래퍼 역할, `IndexingService`와 `KnowledgeSearchService`가 검색 기능 담당 |
| domain | 경로 정규화, 해시 검증, 파일 허용 정책, ignored/conflict 파일 판정, frontmatter 파싱, chunking, 검색 필터 정규화 |
| scripts | vault/archive 파일 백업, soft-delete cleanup, 수동 업로드/저장/목록 보조 |

### 1.2 Revision 기반 양방향 동기화

`obsisync sync`는 다음 순서로 동작한다.

1. 디바이스 등록
2. 서버 변경 pull
3. 로컬 vault 스캔 및 SHA-256 해시 비교
4. new/modified/deleted push
5. 다시 pull해서 cursor 전진 및 자신의 push 이벤트 반영
6. manifest atomic write
7. 선택적으로 Obsidian Local REST API refresh

서버는 vault 전역 `current_revision`을 단조 증가시키고, 파일별 `vault_files.revision`을 클라이언트의 `base_revision`과 비교한다. `base_revision=0`은 신규 생성 또는 soft-delete 경로 재생성을 의미한다. 같은 revision과 같은 hash를 다시 보내면 idempotent no-op으로 처리한다.

충돌은 `base_revision != row.revision`일 때 409 `SYNC_CONFLICT`로 반환되며 `sync_conflicts`에 `OPEN`으로 기록된다. 에이전트는 현재 `manual`, `local-wins`, `remote-wins` 정책을 지원한다.

| 정책 | 동작 |
|---|---|
| `manual` | 원본 파일을 덮지 않고 `.conflict.{device_id}.{timestamp}` 파일 생성 |
| `local-wins` | 서버 최신 revision을 기준으로 로컬 내용을 재push |
| `remote-wins` | 로컬 변경을 backup conflict 파일로 남기고 서버 내용을 로컬에 적용 |

삭제는 soft delete다. 삭제 시 `vault_files.deleted=true`, `status='archived'`, `index_status='skipped'`가 되고 관련 `knowledge_chunks`는 즉시 삭제된다. canonical 파일은 retention 기간 동안 남아 있다가 cleanup 스크립트로 `archives/`로 이동된다. Markdown은 `vault_file_versions`를 통해 restore할 수 있지만, 첨부파일은 버전 전문을 저장하지 않으므로 restore 대상이 아니다.

### 1.3 첨부파일, watch, 재시도

현재 코드와 `docs/sync-agent.md` 기준으로, Revision Sync API와 에이전트는 Markdown뿐 아니라 옵션으로 이미지/PDF 첨부파일 동기화를 지원한다. 단, MCP `sync_file` one-way upload는 Markdown only다.

| 종류 | 확장자 | 특성 |
|---|---|---|
| Markdown | `.md` | UTF-8 content, 버전 히스토리 및 restore 가능, vectorizing 대상 |
| 이미지 | `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp` | base64 JSON 전송, 버전 히스토리 없음, vectorizing 제외 |
| PDF | `.pdf` | base64 JSON 전송, 버전 히스토리 없음, vectorizing 제외 |

watch 모드는 `watchdog` 기반으로 vault 변경을 감지하고 debounce 후 sync를 실행한다. sync가 pull 과정에서 직접 쓴 파일 이벤트가 다시 push를 유발하지 않도록 실행 중 이벤트 무시와 실행 직후 drain 구간을 둔다. 주기적 안전망 interval도 설정할 수 있어 서버 쪽 변경만 발생하는 경우를 보완한다.

클라이언트는 HTTP 재시도 설정도 갖고 있다. 네트워크 오류, 408, 429, 5xx 같은 일시적 실패에는 지수 백오프와 `Retry-After`를 적용하고, 409 `SYNC_CONFLICT`는 재시도 대상이 아니라 충돌 정책으로 넘긴다. 현재 retry에는 jitter가 없으므로 여러 agent가 동시에 복구될 수 있는 배포에서는 운영 리스크로 남는다.

### 1.4 MCP one-way save와 revision 통합

최근 코드 기준으로 `VaultSyncService.force_sync_file`은 더 이상 revision 체계를 조용히 우회하지 않는다. MCP `sync_file`은 다음 정책을 갖는다.

- 기본값은 `overwrite=false`다. 기존 파일이 있고 content가 다르면 409 `CONFLICT_DETECTED`로 실패한다.
- 기존 파일의 저장소 hash가 DB `content_hash`와 다르면 `stored_file_hash_mismatch`로 실패한다.
- 동일 content는 새 revision 없이 `skipped`로 끝난다.
- `overwrite=true`일 때만 명시적으로 덮어쓰며, 이때도 `RevisionSyncService.force_put_file()`에 위임한다.
- `force_put_file()`은 optimistic `base_revision` 검사는 생략하지만, vault revision 증가, `sync_events` 기록, Markdown `vault_file_versions` 저장, atomic staged write는 일반 `put_file()`과 같은 `_write_revision()` 경로를 사용한다.
- 따라서 revision client는 다음 pull에서 MCP write를 `CREATE`/`UPDATE` 이벤트로 관측한다.

이 동작은 `tests/test_mcp_vault_sync.py`에서 생성, 기본 conflict, 명시 overwrite, revision client 가시성, 동일 content no-op까지 회귀 테스트로 고정되어 있다.

### 1.5 강점

- 서버 권위 revision, 파일별 optimistic concurrency, DB 행 잠금, unique revision 제약으로 동시 쓰기 정합성이 강하다.
- `sync_events`, `sync_devices`, `sync_conflicts`, `vault_file_versions`가 있어 변경 추적과 복구 모델이 명확하다.
- Markdown full-text 버전 히스토리와 soft delete/restore를 제공한다.
- MCP `sync_file`도 fail-closed 기본값과 opt-in overwrite 정책을 가지며, 실제 write는 revision/event/version 경로에 통합되어 있다.
- 이미지/PDF 첨부파일의 실제 binary 양방향 동기화를 지원한다. 비교 대상 플러그인은 attachment URL 참조 중심이다.
- 토큰을 평문 저장하지 않고 해시 저장하며, admin token과 DB API token의 용도를 분리한다.
- 경로 검증이 방어적이다. traversal, 절대경로, 백슬래시, Windows 예약어, trailing dot/space, segment byte length 등을 제한한다.
- pgvector/Ollama 기반 검색, MCP 도구, semantic search가 있어 단순 sync 이상의 지식 저장소 기능을 가진다.
- 동시 PUT, stale revision, soft delete/restore, attachment base64, request size, watch debounce/gate, conflict policy, MCP revision 통합 테스트가 있다.

### 1.6 한계와 리스크

- `VaultSyncService.force_sync_file`은 이제 revision/event/version 경로에 통합되어 있지만, `overwrite=true`는 여전히 optimistic `base_revision` 없이 의도적으로 최신 서버 내용을 대체한다. 권한(`api_tokens.allow_overwrite`)과 감사(`sync_events.origin`, audit 로그)는 P1로 구현됐고(아래 P1 참고), UI 경고 정책은 P2 플러그인 몫으로 남아 있다.
- MCP save workflow는 `sync_file -> reindex_vault`의 2단계다. write는 revision에 통합되었지만, 검색 가능 상태는 reindex 호출이 따라와야 완성된다.
- reindex는 sync와 자동 연결되어 있지 않아 별도 호출이나 스케줄링이 필요하다.
- Markdown `vault_file_versions`는 retention/압축 정책이 없어 장기적으로 무한 증가할 수 있다.
- `backup_vaults.py`는 vault/archive 파일 백업 중심이고 PostgreSQL 백업까지 포함하지 않는다.
- frontmatter 파서는 자체 YAML subset이며, revision sync 코어와 MCP write 경로에 비해 frontmatter/indexing/search 테스트는 상대적으로 약하다.
- 플러그인 UI가 없어 CLI 설치, token/env/config 파일, watch 프로세스 운영이 사용자에게 노출된다.
- 모바일 환경에서는 Python CLI/watch agent를 직접 실행할 수 없으므로 Obsidian 모바일 vault 동기화 UX가 약하다.
- 대용량 첨부파일은 base64 JSON payload라 전송 오버헤드가 있고, 플러그인 클라이언트로 옮길 때는 모바일/메모리 제약을 별도로 검증해야 한다.

---

## 2. obsidian-auto-note-importer 기능 분석

### 2.1 목적과 플러그인 구조

비교 대상은 Airtable, SeaTable, Supabase의 원격 row/record를 Obsidian note로 가져오고, Obsidian frontmatter 변경을 다시 원격 DB로 push하는 Obsidian 플러그인이다.

핵심 구조는 다음과 같다.

```text
AutoNoteImporterPlugin
  -> ConfigManager
  -> ConfigInstance per config
     -> DatabaseProvider
     -> FileWatcher
     -> SyncQueue
     -> SyncOrchestrator
     -> ConflictResolver
```

다중 config를 지원하며, credential과 config를 분리한다. 하나의 credential로 여러 table/folder sync config를 만들 수 있고, 각 config는 독립적인 provider, watcher, queue, scheduler를 가진다.

provider 추상화는 `DatabaseProvider`와 provider registry로 구성된다. provider factory, field mapper, credential form renderer를 credential type별로 등록하므로 Airtable/SeaTable/Supabase를 같은 orchestrator에서 처리한다.

### 2.2 Obsidian API와 UX

플러그인은 Obsidian API를 적극적으로 사용한다.

| 영역 | 사용 방식 |
|---|---|
| 설정 저장 | `loadData()`, `saveData()` |
| 설정 UI | `PluginSettingTab`, `Setting`, provider별 credential form |
| 명령 | `addCommand()`와 `checkCallback`으로 config별 pull/push/bidirectional command 등록 |
| 파일 | `vault.read/create/modify/createFolder`, `adapter.exists`, `getAbstractFileByPath` |
| 변경 감지 | `vault.on('modify', ...)` |
| 현재 파일 | `workspace.getActiveFile()`, `getActiveViewOfType(MarkdownView)` |
| metadata | `metadataCache.getFileCache(file)?.frontmatter` |
| 알림 | `Notice`, status bar item |
| HTTP | Obsidian `requestUrl()` 사용 |

사용자는 설정 탭에서 provider credential을 등록하고 test connection을 실행하며, base/table/view/field를 dropdown으로 선택한다. metadata 조회가 실패하면 text fallback을 제공한다. Supabase는 publishable key와 schema introspection 제한을 감지하고, 필요한 RPC setup SQL을 UI에서 안내한다.

### 2.3 파일 생성, 템플릿, Bases

pull all은 원격 record를 fetch한 뒤 destination folder를 만들고, `primaryField` frontmatter 기준으로 중복을 판단하며, note를 생성하거나 업데이트한다. filename field와 subfolder field를 설정할 수 있고 값은 provider별 안전 타입/문자 검증을 거친다.

note 생성은 두 방식이다.

- 템플릿이 있으면 `{{fieldName}}`, `{{Attachment.0.url}}` 같은 placeholder를 치환한다.
- 템플릿이 없으면 모든 field를 YAML frontmatter로 만들고 기본 본문 섹션을 생성한다.

YAML 포맷은 Obsidian Bases 호환성을 의식한다. number/boolean은 native type으로 유지하고, multiline string은 block scalar를 사용하며, object-like 값은 축약한다. 필요하면 `.base` 파일을 생성해 imported notes를 Obsidian table/card workflow로 바로 볼 수 있게 한다.

push는 Markdown 본문 전체가 아니라 frontmatter의 syncable field를 원격 DB에 반영한다. `primaryField` 같은 system field는 제외하고, field metadata가 있으면 read-only/computed/unsupported field도 제외한다.

### 2.4 동기화와 충돌 처리

동기화 모드는 pull, push, bidirectional로 나뉜다. bidirectional은 push 후 계산 필드가 원격에서 반영될 시간을 기다린 뒤 다시 pull한다.

파일 변경 감지는 Obsidian vault modify event 기반이다. watcher는 sync folder 안의 Markdown 변경만 pending set에 넣고 debounce 후 queue에 넘긴다. `SyncQueue`는 같은 mode/scope 요청의 file path를 merge하고 순차 처리해 race를 줄인다. `externalSyncing`과 `internalSyncing` 플래그로 pull/자체 sync가 쓴 파일 이벤트를 다시 sync하지 않게 한다.

충돌 감지는 push 직전 provider에서 현재 원격 record를 fetch하고, frontmatter field 값과 비교하는 방식이다.

| 모드 | 동작 |
|---|---|
| `manual` | 충돌 field를 Notice로 알리고 push 중단 |
| `obsidian-wins` | conflict detection을 생략하고 Obsidian 값을 push |
| `remote-wins` | 충돌 field는 제외하고 non-conflicted field만 push |

이는 사용자 UX는 좋지만, 서버 revision cursor를 기반으로 한 강한 낙관적 잠금은 아니다.

### 2.5 강점과 한계

강점은 Obsidian-native UX와 provider별 방어적 설계다.

- 설정 탭, credential test, metadata dropdown, fallback text input, command palette, Notice/status bar가 잘 갖춰져 있다.
- provider registry 덕분에 외부 DB 추가 경로가 명확하다.
- field type mapper가 unknown/read-only/computed/object-shaped field를 fail-closed로 다룬다.
- filename/subfolder sanitizer가 강하고, provider별 안전 타입만 허용한다.
- file watcher + debounce + queue + syncing flag로 실시간 UX와 자기 루프 방지를 제공한다.
- Obsidian Bases와 템플릿 지원이 record-to-note workflow에 실용적이다.

한계는 다음과 같다.

- full-file revision sync가 아니라 frontmatter field 중심 sync다.
- pull update가 note content를 다시 빌드하므로, 사용자가 본문을 직접 편집한 경우 overwrite 정책에 따라 손실 가능성이 있다.
- record/file rename reconciliation이 제한적이다.
- binary attachment sync는 URL 참조 중심이며 실제 파일 버전/복구 모델이 없다.
- 장시간 background daemon, 대용량 파일, 서버급 indexing/embedding에는 플러그인 환경이 적합하지 않다.
- credential은 Obsidian plugin data에 저장되며 OS keychain 수준의 secret store가 아니다.

---

## 3. 기능별 상세 비교

| 기능 영역 | obsidian-sync | auto-note-importer | 평가 |
|---|---|---|---|
| 동기화 정합성 | 서버 revision, 파일별 `base_revision`, 행 잠금 | push 직전 원격 값 diff | `obsidian-sync` 우위 |
| 충돌 해소 | `manual`, `local-wins`, `remote-wins`; conflict/backup 파일 | `manual`, `obsidian-wins`, `remote-wins`; field-level Notice | 코어는 `obsidian-sync`, UI는 importer 우위 |
| 변경 감지 | CLI scan + watchdog watch | Obsidian `vault.on('modify')` | 플러그인 UX는 importer 우위 |
| 자기 쓰기 루프 방지 | watch 실행 중 무시 + drain | external/internal syncing flag | 둘 다 보유 |
| 재시도 | agent HTTP retry/backoff/Retry-After | provider/rate limiter 기반 retry/backoff | 둘 다 보유, provider API는 importer가 세밀 |
| 첨부파일 | Sync API/agent는 이미지/PDF 실제 binary sync 지원, MCP upload는 Markdown only | attachment URL 참조 중심 | `obsidian-sync` 우위 |
| 삭제/복구 | soft delete, archive, Markdown restore | 원격 record 기준, 별도 file history 없음 | `obsidian-sync` 우위 |
| 버전 히스토리 | Markdown full-text version 저장 | 없음 | `obsidian-sync` 우위 |
| 인덱싱/검색 | pgvector/Ollama/MCP/search logs | 없음 | `obsidian-sync` 우위 |
| MCP/agent save | fail-closed 기본값, opt-in overwrite, revision event로 pull 가능 | 해당 없음 | `obsidian-sync` 고유 강점 |
| 설정 UX | config/env/CLI 중심 | Obsidian 설정 탭과 command palette | importer 우위 |
| 모바일 | CLI 실행 불가, 서버 API는 가능 | `isDesktopOnly: false` 플러그인 | importer 우위 |
| provider 확장성 | 자체 서버 단일 백엔드 | provider registry | importer 우위 |
| frontmatter/YAML | 자체 subset parser, indexing 목적 | Bases 호환 formatting과 template | importer 우위 |
| 보안 | token hash 저장, admin/client 분리 | plugin data 저장, secret key warning | 저장 모델은 `obsidian-sync`, UX 경고는 importer 우위 |
| 테스트 | revision sync, agent, watch, attachment, MCP revision 통합 흐름 강함 | provider/UI/core 단위/E2E 폭넓음 | 관심 영역이 다름. `obsidian-sync`는 indexing/search/frontmatter 보강 필요 |

---

## 4. 개선 제안

### P1. 명시 overwrite 정책과 감사성 강화

> 구현 완료(2026-07-12): 아래 항목 중 서버 측 과제는 모두 구현됐다. UX 항목만 P2 플러그인 몫으로 남는다.

가장 큰 구조 리스크였던 MCP `sync_file`의 revision 우회는 현재 코드에서 상당 부분 해소됐다. 이제 남은 과제는 `overwrite=true`라는 강제 대체 경로의 운영 정책을 명확히 하는 것이다.

- 기본 쓰기는 지금처럼 fail-closed를 유지한다. — 유지됨.
- `overwrite=true`는 별도 권한 또는 token scope로 제한할지 검토한다. — 구현됨: `api_tokens.allow_overwrite`(신규 토큰 기본 false, 기존 토큰은 마이그레이션에서 true 백필). 권한 없는 토큰의 `overwrite=true`는 파일 존재 여부와 무관하게 403.
- `sync_events`에는 이미 남지만, 사람이 감사하기 쉬운 별도 overwrite audit field/log를 둘지 검토한다. — 구현됨: `sync_events.origin` 컬럼(`'mcp'`/`'mcp_overwrite'`, 기존 revision API 경로는 NULL 유지) + 강제 대체 시 이전/새 revision·hash와 token_id를 담은 INFO audit 로그.
- Obsidian 플러그인이나 MCP 클라이언트에서는 overwrite 전에 서버 revision/content hash를 보여주고 확인을 받는 UX가 필요하다. — P2 플러그인 범위로 이관.
- 장기적으로 MCP save API도 가능하면 `base_revision`을 선택적으로 받을 수 있게 해, 자동화 클라이언트가 더 엄격한 optimistic write를 선택할 수 있게 한다. — 구현됨: `McpSyncFileRequest.base_revision` 옵션. 제공 시 revision API의 `put_file`을 재사용해 동일한 optimistic 의미론(stale이면 409 `SYNC_CONFLICT` + `sync_conflicts` 기록, 0은 신규 생성)을 적용하며, `overwrite=true`와는 상호 배타(400)다.

### P2. 플러그인 클라이언트 추가

서버를 플러그인으로 바꾸는 것이 아니라, TypeScript Obsidian 플러그인을 추가 클라이언트로 두는 것이 적절하다.

1차 범위는 다음 정도가 현실적이다.

- server URL, vault ID, device ID, API token 설정
- connection test와 `/sync/status` 표시
- command palette: sync now, status, pull, push
- Obsidian `vault.on('modify')` 기반 watch toggle
- conflict 목록/알림
- 기존 `.obsidian-sync-agent/manifest.json` 호환 또는 migration
- attachment sync toggle과 size warning
- reindex changed-only 수동 버튼 또는 sync 후 선택적 trigger

2차 범위는 다음이다.

- conflict resolution UI: local-wins, remote-wins, manual file open
- pending vectorizing/reindex status와 reindex trigger
- 검색/MCP helper UI
- sync status dashboard note 또는 `.base` 생성

CLI는 그대로 유지해야 한다. 플러그인은 Obsidian 실행 중 UX와 모바일을 담당하고, CLI/watch는 헤드리스 자동화, 서버/대용량 처리, launchd/cron 운영을 담당하는 식으로 역할을 나누는 것이 낫다.

### P3. 플러그인화 시 manifest/source of truth 결정

플러그인과 CLI가 같은 vault에서 번갈아 실행될 수 있다. 따라서 로컬 상태 저장을 두 벌로 만들면 충돌 가능성이 커진다.

권장안은 플러그인이 기존 `.obsidian-sync-agent/manifest.json` 포맷을 읽고 쓰는 것이다. Obsidian plugin `loadData()`에는 UI 설정만 저장하고, 파일별 sync cursor/hash/conflict 상태는 CLI와 공유한다. 단, 모바일에서는 숨김 폴더 쓰기와 plugin sandbox 동작을 별도로 검증해야 한다.

같은 vault에서 CLI watch와 플러그인 watcher를 동시에 켜면 중복 sync와 race가 생길 수 있다. 플러그인 MVP에는 “이 vault는 CLI/watch와 병행하지 않음” 같은 명시적 모드 선택이나 lock file 정책을 넣는 편이 안전하다.

### P4. 운영 보강

- `vault_file_versions` retention 정책 추가: 파일당 최근 N개 + 기간 기반 pruning을 조합한다.
- PostgreSQL 백업을 운영 스크립트에 포함한다.
- reindex 자동화 옵션을 제공한다. 예: sync 성공 후 pending 파일 수가 있으면 background reindex queue에 enqueue.
- retry backoff에 jitter를 추가해 다중 agent 복구 시 동시 재시도를 줄인다.
- search/index/MCP/VaultSyncService 테스트를 revision sync 코어 수준까지 보강한다.
- frontmatter parser를 PyYAML `safe_load` 기반으로 바꾸거나, 자체 subset 유지 시 테스트를 늘린다.

### P5. 비교 대상에서 가져올 UX 패턴

- credential과 vault config 분리
- 설정 UI의 test connection, status preview, validation banner
- command palette의 조건부 표시
- Notice/status bar 기반 짧은 진행 상태
- provider 플러그인화 자체는 지금은 과하다. 다만 향후 Notion/Custom API 같은 외부 source import를 추가한다면 importer의 provider registry 패턴을 참고할 가치가 있다.
- Bases 아이디어는 직접적인 sync 기능으로는 필수는 아니지만, sync status/search result/dashboard를 `.base` 또는 Markdown dashboard로 생성하는 기능은 유용할 수 있다.

---

## 5. 플러그인 적용 판단

### 플러그인 적용이 좋은 이유

- 사용자가 CLI/env/config 파일 없이 Obsidian 안에서 설정할 수 있다.
- Obsidian event API로 변경 감지가 자연스럽고, 별도 watchdog 프로세스가 필요 없다.
- 모바일 vault 동기화 가능성이 열린다.
- Notice/status bar/command palette로 sync 상태와 conflict를 즉시 보여줄 수 있다.
- 현재 서버 API가 HTTP 기반이므로 TypeScript 클라이언트 구현이 가능하다.
- 현재 프로젝트의 revision sync 모델은 비교 대상보다 강하므로, 플러그인은 코어를 약화시키지 않고 UX만 보강하는 방향이 가능하다.

### 주의할 점

- Python agent의 sync engine을 TypeScript로 재구현해야 하므로 구현량이 작지 않다.
- Obsidian 플러그인은 장시간 background daemon이나 대용량 파일 처리에 CLI보다 불리하다.
- API token 저장은 Obsidian plugin data에 의존하게 되므로 보안 안내와 token scope 분리가 필요하다.
- attachment base64 upload, request size, retry/backoff, conflict file 생성, atomic write를 Obsidian API 제약 안에서 다시 검증해야 한다.
- CLI와 플러그인이 같은 vault를 공유할 때 manifest 호환성이 중요하다.

### 최종 판단

플러그인 적용은 “대체”가 아니라 “보완”으로 추진하는 것이 맞다.

| 클라이언트 | 권장 역할 |
|---|---|
| Python CLI/watch agent | 헤드리스 자동화, launchd/cron, 대용량/서버형 운영, 디버깅, 안정적 파일시스템 처리 |
| Obsidian 플러그인 | 설정 UI, command palette, 실시간 사용자 피드백, 모바일, conflict 확인/해소 UX |

따라서 우선순위는 `P1 명시 overwrite 정책과 감사성 강화`를 먼저 처리하고, 그 다음 `P2 플러그인 클라이언트 MVP`를 별도 패키지로 시작하는 순서가 좋다.

---

## 6. 도입하지 않아도 되는 것

- 비교 대상의 provider registry를 현재 sync core에 바로 도입할 필요는 없다. 현재 프로젝트는 자체 서버 단일 백엔드가 명확하다.
- push 직전 재조회식 conflict detection은 현재 revision 모델보다 약하다. 참고할 대상은 conflict UX이지 conflict core가 아니다.
- 레코드 템플릿/Bases 중심 note generation은 파일 동기화 서비스의 핵심 관심사가 아니다. 다만 dashboard/status artifact 생성에는 응용할 수 있다.
- 서버 기능을 플러그인 내부로 옮기는 방향은 부적절하다. PostgreSQL, pgvector, Ollama, MCP는 서버에 남아야 한다.
