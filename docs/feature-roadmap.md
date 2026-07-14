# 기능 분석 및 사용성 개선 로드맵

> 작성일: 2026-07-14 / 갱신: 2026-07-14 (Go conflict_policy·watch, 리비전 GC 구현 반영)
> 대상: FastAPI 백엔드(`src/obsidian_sync/`), Go 동기화 에이전트(`cmd/`, `internal/syncagent/`), Python 호환 CLI(`src/obsidian_sync/sync_agent/`), MCP 도구
> 관점: 결함이 아닌 **기능 공백** 중심. 결함·견고성 이슈는 [service-defect-usability-analysis.md](service-defect-usability-analysis.md) 참조.

---

## 1. 현재 기능 요약

### 서버 (FastAPI)

- **Vault 관리**: 생성/목록 조회, 디바이스 등록, 토큰 발급·조회·폐기(권한 세분화: `allow_overwrite`)
- **리비전 기반 동기화 API**: 변경 이벤트 커서 조회, 파일 GET/PUT/DELETE(soft delete)/restore, 낙관적 잠금(`base_revision`) + 409 conflict
- **지식 검색**: bge-m3 임베딩(Ollama) + pgvector HNSW + PostgreSQL FTS의 하이브리드(RRF), 선택적 LLM rerank, per-source 다양성 캡, 검색 피드백 수집(`request_id`), 검색 로그 조회
- **인덱싱**: `reindex_vault(full|changed_only)`, 인덱스 신선도 신호(`pending/failed/model_stale`)

### MCP 도구 (7종)

`list_vaults`, `sync_file`, `get_note`, `get_sync_status`, `reindex_vault`, `search_knowledge`, `submit_search_feedback`

### 로컬 에이전트

- **Go `obsisync`** (프로덕션 기본 배포): `status` / `sync` / `watch` / `update`, `conflict_policy`(manual/local-wins/remote-wins), GET·기기등록 transient 재시도
- **Python CLI** (호환·롤백 경로): `sync` / `status` / `watch`, `conflict_policy`, 전 요청 재시도(지수 백오프)

2026-07-14 기준 Go/Python 기능 격차는 대부분 해소됐다. 남은 차이: Python은 전 요청 재시도 + 백오프 설정 노출, Go는 안전한 요청(GET/기기등록)만 고정 지연 재시도.

### 저장은 되지만 노출되지 않은 자산

데이터 계층에 이미 존재하나 조회·활용 인터페이스가 없는 기능이 많다:

| 자산 | 위치 | 현재 상태 |
|---|---|---|
| 리비전별 전문 히스토리 | `vault_file_versions` | 쌓이기만 함 — 조회/비교/복원 API 없음 (2026-07-14: 보존 기간 기반 pruning은 구현됨) |
| soft-delete 레코드 | `vault_files.deleted_at` | restore API는 있으나 삭제 목록 조회 불가 |
| archive 테이블 | `ArchivedVaultFile`/`ArchivedKnowledgeChunk` | ~~테이블만 존재, populate 코드 없음~~ 2026-07-14: cleanup 스크립트가 populate 구현 |
| 검색 피드백 | search feedback 로그 | 수집만 하고 소비처 없음 |

---

## 2. 추가 기능 제안 (사용성 임팩트 순)

### 2.1 동기화 후 자동 백그라운드 인덱싱 (2026-07-14 구현)

**현재**: 성공한 revision 기반 Markdown PUT/RESTORE 또는 MCP `sync_file` 저장이 벡터화 대상이고 `post_sync_indexing_enabled`가 켜져 있으면 `index_status='pending'`으로 표시한 뒤, 같은 프로세스 안에서 best-effort 인덱싱을 예약한다. 저장 요청 자체는 인덱싱 완료를 기다리지 않으므로 사용자는 "저장하면 곧 검색됨"에 가까운 흐름을 얻고, 검색 JOIN 조건은 여전히 stale 청크 노출을 막는다.

**운영 한계**: 이 예약은 아직 durable queue가 아니라 in-process 작업이다. 프로세스가 재시작되면 실행 중이거나 대기 중이던 작업은 사라질 수 있지만, `vault_files.index_status='pending'` 행은 DB에 남으므로 이후 명시적인 `reindex_vault` 또는 `/reindex` 호출로 복구할 수 있다. 자동 재시도, 멀티 프로세스 내구성, 배치 처리, 동시성 제어가 있다고 보지는 않는다.

**남은 작업**: full rebuild와 실패 재처리는 기존 수동 `reindex_vault(full|changed_only)`가 계속 담당한다. durable queue가 도입되기 전까지 운영자는 restart 이후 pending/failed 상태를 보고 필요할 때 명시적으로 reindex를 실행해야 한다.

### 2.2 리비전 히스토리 노출 — 데이터는 이미 있음

**현재**: `vault_file_versions`에 리비전별 마크다운 전문이 쌓이고 있는데 꺼내 볼 방법이 없다.

**제안** (추가 비용 대비 효과가 가장 큰 영역):

- `GET /sync/files/{path}/versions` — 리비전 목록(시각, 크기, 해시)
- `GET /sync/files/{path}/versions/{rev}` — 특정 시점 내용 조회 (diff는 클라이언트 몫으로 둬도 충분)
- 특정 리비전으로 되돌리기 — 내부적으로는 해당 내용으로 새 리비전 PUT (리비전 체인 불변식 유지)
- MCP `get_note_history` 도구 — 에이전트가 "이 노트 어제 버전이랑 뭐가 달라졌지"에 답할 수 있게 됨

**연계**: 리비전 GC(pruning)는 2026-07-14 구현됐다(`scripts/cleanup_deleted_files.py`, `sync_version_retention_days` 기본 90일, 경로별 최신 리비전 보존). 이제 남은 반쪽이 조회 인터페이스다 — 조회 없이 GC만 있는 현재 상태는 데이터를 버리기만 하는 셈이므로, 히스토리 API를 붙일 때 보존 기간 설정과 함께 안내해야 한다(보존 기간 밖 리비전은 조회 불가).

### 2.3 휴지통 UX

**현재**: soft delete와 restore API는 있지만 **삭제된 파일 목록을 볼 방법이 없다**. restore가 사실상 "삭제한 경로를 정확히 기억할 때만" 쓸 수 있는 기능이다.

**제안**:

- `GET /sync/files?deleted=true` — 삭제 시각·보존 만료일 포함 목록
- MCP `list_deleted_notes` 도구

이 둘만 있으면 기존 restore 기능이 실제로 쓰이는 기능이 된다.

### 2.4 MCP 브라우징 도구: `list_notes`

**현재**: 에이전트가 vault 내용에 접근하는 경로는 검색(`search_knowledge`) 아니면 정확한 경로의 `get_note`뿐이다. 검색은 질의가 명확할 때만 유효하다.

**제안**: 폴더/태그/frontmatter/수정 시각 기준 노트 목록 조회.

```
list_notes(vault_id, folder=None, tag=None, modified_since=None, limit=...)
```

"최근 일주일 작성 노트 정리해줘", "Projects 폴더에 뭐 있지" 같은 **탐색형** 요청이 가능해진다. 브라우징은 검색과 별개의 접근 축이다.

### 2.5 Go 에이전트 완성: ~~`watch`~~ + `init` + 진행률

가장 큰 항목이었던 `watch`는 2026-07-14 구현됐다(`obsisync watch` — fsnotify 재귀 감시 + debounce + 자체 트리거 루프 방지 게이트). 준실시간 동기화를 위해 Python CLI를 설치해야 하는 상황은 해소됐다.

남은 항목:

1. **`init`** — config.json 대화식 생성 (현재는 손으로 작성해야 함)
2. **진행률 출력** — 대형 vault 초기 sync 시 멈춤/진행 구분 (엔진에 로깅 호출 자체가 없음; watch 모드가 생기면서 장시간 실행 시 관찰 가능성의 가치가 더 커짐)

### 2.6 부분 수정 MCP 도구: `append_to_note` / `patch_note`

**현재**: `sync_file`은 전문 교체 방식이다. 에이전트가 노트에 한 줄 추가하려 해도 `get_note` → 전문 재구성 → `sync_file` 왕복이 필요하고, 그 사이 conflict 창이 열리며, 전문을 두 번 전송하는 토큰 비용이 든다.

**제안**: 서버 측에서 `base_revision` 검증 후 append 또는 섹션 단위 삽입을 수행하는 도구. `RevisionSyncService`를 통과하므로 리비전 불변식은 그대로 유지된다(서버가 기존 내용을 읽어 병합 후 정규 PUT 경로로 저장).

**효과**: knowledge-management 스킬처럼 "에이전트가 학습 내용을 기록하는" 시나리오 — 이 서비스의 주 사용 시나리오 — 의 안정성과 비용이 크게 개선된다.

### 2.7 검색 품질·편의

- **한국어 형태소/bigram 토크나이저** (결함 분석 §3.5): 현재 `to_tsvector('simple', ...)`는 조사 변형("임베딩"/"임베딩은"/"임베딩을")을 다른 렉심으로 취급해 lexical leg가 한국어에서 제 역할을 못 한다. pg_bigm 또는 mecab 기반 tsvector 설정 검토.
- **`modified_since`/날짜 범위 필터**: "이번 달에 쓴 노트 중에서" 같은 시간 축 질의 지원.
- **유사 노트 추천** `find_similar(path)`: 청크 임베딩이 이미 있으므로 질의 임베딩 없이 노트 기준 관련 노트를 반환할 수 있다. 지식 연결 발견이라는 Obsidian 본연의 가치와 맞닿는 기능.
- **검색 피드백 활용**: 현재 피드백은 쌓이기만 한다. 최소한 피드백 통계 조회 엔드포인트를 추가하고, 나아가 rerank 부스팅 신호로 사용.

### 2.8 Conflict 해결 경험

**현재** (2026-07-14 갱신): Go에도 `conflict_policy`(local-wins/remote-wins)가 생겨 **자동 해결을 선택한 사용자**의 경험은 해소됐다. 그러나 기본값인 `manual`의 경험은 그대로다 — conflict 파일 생성 + exit 1이 전부이고, 어떤 conflict가 미해결로 남아 있는지 모아 볼 수 없으며, 해결 절차는 문서를 찾아 읽어야 안다.

**제안** (단계적, manual 사용자 대상):

1. 서버/CLI에서 미해결 conflict 목록 조회 — `obsisync conflicts` (manifest의 conflicts 맵이 이미 추적 중이므로 출력만 붙이면 됨)
2. conflict 발생 시 해결 절차 한 줄 안내 출력 (`printSummary` 확장)
3. (장기) `obsisync resolve --local|--remote <path>` 대화식 해결 — conflict_policy 이식으로 서버 재push/로컬 백업 로직이 이미 존재하므로 이를 경로 단위로 재사용

동기화 도구에서 사용자 스트레스가 가장 큰 순간이 conflict이므로, 이 지점의 경험 개선 체감이 크다.

---

## 3. 권장 착수 순서

| 순서 | 기능 | 상태 / 근거 |
|---|---|---|
| 1 | 자동 백그라운드 인덱싱 (§2.1) | 구현됨(2026-07-14). 성공한 Markdown PUT/RESTORE/MCP `sync_file` 후 설정이 켜져 있으면 in-process best-effort 예약. durable queue 전까지 restart 후 pending은 수동 reindex로 복구 |
| 2 | ~~Go `watch`~~ + `init` (§2.5) | watch 완료(2026-07-14). `init`·진행률 남음 |
| 3 | 휴지통 목록 + 리비전 히스토리 API (§2.2, §2.3) | 미착수 — GC 구현으로 보존 정책은 갖춰짐. 조회 인터페이스가 남은 반쪽 |
| 4 | MCP `list_notes` + `append_to_note` (§2.4, §2.6) | 미착수 — 에이전트 활용(주 사용 시나리오) 폭 확장 |
| 5 | 한국어 lexical 개선, 유사 노트, conflict UX (§2.7, §2.8) | 미착수 — conflict UX는 자동 해결(정책) 완료, manual 경험 개선만 남음 |

구현 시 공통 제약: 모든 vault 쓰기는 `RevisionSyncService`를 통과해야 하며(리비전·이벤트·스토리지·롤백 불변식), 스키마 변경은 Alembic 리비전으로만 한다. Go/Python 에이전트 프로토콜 변경은 양쪽 동시 반영이 원칙이다.
