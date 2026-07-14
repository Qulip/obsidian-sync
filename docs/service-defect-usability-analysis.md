# 서비스 결함 및 실사용 불편 분석

> 작성일: 2026-07-14
> 대상: FastAPI 백엔드(`src/obsidian_sync/`), Go 동기화 에이전트(`cmd/`, `internal/syncagent/`), Python 호환 CLI(`src/obsidian_sync/sync_agent/`), 벡터화·검색·MCP 파이프라인
> 분석 방법: 영역별 3개 서브에이전트 정적 분석 후, CRITICAL/HIGH 항목은 해당 코드 위치를 직접 재확인. 2026-07-14 재검증 후 상위 결함 일부를 코드 수정 및 회귀 테스트로 보완. 실행 재현 없이 코드 구조로부터 추론한 항목은 "추정"으로 표기.

---

## 요약 결론

동시성 제어(행 잠금 + 원자적 리비전 증가 + unique 제약 + 스토리지 롤백), 경로 traversal 방어, SQL 파라미터 바인딩, 토큰 해시 저장 등 핵심 안전장치는 견고하게 구현되어 있다. 반면 다음 결함은 실사용에서 데이터 혼란 또는 기능 공백으로 직결된다.

| 우선순위 | 결함 | 심각도 | 상태 |
|---|---|---|---|
| 1 | reindex 레이스로 stale `content_hash` 저장 → 청크 영구 검색 누락 | HIGH | **수정됨**: fresh markdown hash 사용 + 회귀 테스트 |
| 2 | manifest를 run 종료 시에만 저장 → 중단 후 재실행 시 허위 conflict | CRITICAL | **수정됨**: Go/Python mutation 직후 증분 저장 + 중단 회귀 테스트 |
| 3 | Go pull에서 심링크 하나로 전체 sync 영구 중단 | HIGH | **수정됨**: warning + skip, 같은 run의 push 제외 + 회귀 테스트 |
| 4 | MCP 도구 에러에서 구조화 정보(`code`/`details`) 전부 소실 | HIGH | **수정됨**: Streamable MCP AppError 구조화 envelope 반환 + 회귀 테스트 |
| 5 | Go 에이전트에 재시도·conflict_policy·watch 부재 (Python과 기능 격차) | CRITICAL~HIGH | **부분 수정**: Go GET/기기등록 transient retry 추가. conflict_policy/watch는 남음 |
| 6 | 리비전/소프트 삭제 GC가 문서상으로만 존재 | MEDIUM | **부분 정정**: soft-delete cleanup script 존재. archive/version pruning은 남음 |

### 2026-07-14 재검증 및 수정 반영

- `src/obsidian_sync/services/indexing.py`는 chunk 저장 시 `record.content_hash` 대신 방금 읽은 markdown의 `sha256_text(markdown)`을 사용하도록 수정했다. 회귀 테스트: `tests/test_indexing_content_hash.py`.
- Go/Python sync-agent 모두 파일 적용·삭제·push 성공·conflict 기록 등 manifest 상태가 변하는 지점 직후 증분 저장하도록 수정했다. 회귀 테스트: `internal/syncagent/engine/engine_test.go`, `tests/sync_agent/test_engine_manifest_persistence.py`.
- Go pull의 `vaultfs.SafePath` 거부는 전체 run 실패가 아니라 warning + 해당 path skip으로 완화했다. 심링크를 따라 쓰거나 삭제하지 않으며, 같은 run의 push 단계에서도 해당 path를 제외한다. unsafe skip이 있으면 cursor를 전진시키지 않아 다음 run에서도 동일 path를 다시 skip 처리하고, stale manifest가 remote delete로 전파되지 않게 했다. 회귀 테스트: `internal/syncagent/engine/symlink_containment_test.go`.
- Go HTTP client는 `GET` 및 device registration `POST /sync/devices`에 한해 transient network error, 408, 429, 5xx를 최대 3회 재시도한다. `PUT`/`DELETE`는 응답 유실 시 revision 의미가 모호하므로 자동 재시도하지 않는다. 회귀 테스트: `internal/syncagent/client/client_test.go`.
- Streamable MCP tool은 `AppError`를 평문 예외로 잃지 않고 `success:false`, `error.code`, `error.message`, `error.details`, `error.status_code` envelope로 반환한다. `_session`의 rollback 후 변환하므로 실패 transaction은 commit되지 않는다. 회귀 테스트: `tests/test_mcp_server_errors.py`.

---

## 1. CRITICAL — 데이터 혼란으로 직결되는 결함

### 1.1 manifest 지연 저장으로 인한 허위 conflict (Go/Python 공통)

**위치**
- Go: `internal/syncagent/engine/engine.go:36-41` — `runner.run()`이 에러 없이 완료된 경우에만 `manifest.Save(cfg.VaultRoot, state)` 호출
- Python: `src/obsidian_sync/sync_agent/engine.py:81-84` — `SyncApiError` 발생 시 `save_manifest`에 도달하지 못함
- 오판 지점: `internal/syncagent/engine/pull.go:153-187` (`applyWrite`), `pull.go:232-237` (`localDirty`)

**문제**
push→pull 전체 사이클이 성공해야만 manifest가 디스크에 저장된다. pull 루프 도중 일부 파일은 이미 로컬에 정상 기록되었지만(`atomicfile.WriteBytes` 자체는 원자적), 해당 파일들의 새 `content_hash`/`server_revision`은 run 전체가 끝나야 저장된다.

**발생 시나리오**
1. `sync` 실행 중 pull이 10개 파일을 로컬에 반영(디스크 내용은 서버와 일치).
2. 11번째 파일에서 네트워크 오류 → `pull()`이 에러 리턴 → `manifest.Save` 미호출. 디스크 manifest는 stale 해시/커서 유지.
3. 재실행 시 같은 커서부터 같은 변경을 다시 pull. 이미 적용된 10개 파일에 대해 `localDirty`가 stale 해시와 실제 해시(서버와 일치)를 비교 → `dirty=true` 오판.
4. **이미 정상 동기화된 파일에 불필요한 `.conflict.*.md`가 생성**된다. 사용자 관점에서 "아무것도 안 바꿨는데 conflict가 생기는" 가장 혼란스러운 형태의 버그.

push 쪽도 동일 구조: push 성공 후 크래시 시 다음 실행에서 stale `base_revision`으로 재전송된다. `docs/sync-api.md`의 idempotent replay 규칙은 "동일 `base_revision` + 동일 `content_hash`"일 때만 적용되므로, 리비전이 이미 전진한 상태에서는 진짜 409로 처리된다.

**개선 제안**
- 파일 단위(또는 N개 배치 단위)로 manifest 증분 저장. 최소한 pull 단계 완료 시점에 한 번 저장.
- 현재 테스트(`engine_test.go`, `push_conflict_test.go`)에 "run 도중 중단 후 재실행" 시나리오가 없음 — 회귀 테스트 추가 필요.

### 1.2 충돌 자동 해결 정책이 Python에만 존재

**위치**
- Python: `src/obsidian_sync/sync_agent/config.py:49-55` (`ConflictPolicy = 'manual'|'local-wins'|'remote-wins'`), `sync_agent/conflict_resolution.py` 전체
- Go: `internal/syncagent/engine/push.go:147-171` (`pushConflict`), `pull.go:189-230` (`writePullConflict`) — manual 동작만 존재. Go 코드 전체에 `conflict_policy` 참조 없음(grep으로 확인). `cmd/obsidian-sync-agent/main.go:153-164` 플래그 목록에도 `--conflict-policy` 없음.

**문제**
프로덕션 기본 배포인 Go `obsisync`는 항상 manual(conflict 파일 생성 + exit code 1)로만 동작한다. 같은 vault를 여러 기기에서 동기화하며 한쪽은 Go, 한쪽은 Python(`local-wins`)을 쓰면 같은 409가 기기마다 완전히 다르게 처리된다. `docs/sync-agent.md:354-405`는 이 기능을 "Python-only"로만 표기할 뿐, 프로덕션 기본 배포에 이 기능이 없다는 실질적 함의를 강조하지 않는다.

**개선 제안**
Go에 `conflict_policy` 이식, 또는 문서에 기능 격차 매트릭스를 명시하고 혼용 시 주의사항 안내.

---

## 2. HIGH

### 2.1 reindex 레이스로 청크가 검색에서 영구 소실

> 2026-07-14 수정됨: `_index_record`가 chunk write용 `content_hash`를
> `sha256_text(markdown)`으로 fresh 계산한다. 또한 chunk 저장 후 현재
> `vault_files.content_hash`가 fresh hash와 다르면 `indexed`로 표시하지 않고
> `pending`으로 남겨 다음 `changed_only` reindex가 재시도하게 한다. 이 항목은
> 원인 분석 기록으로 유지한다.

**위치**
- `src/obsidian_sync/services/indexing.py:187` — `content_hash=record.content_hash or sha256_text(markdown)`
- `src/obsidian_sync/services/indexing.py:45-58` (목록 스냅샷 조회), `:82-92` (디스크 재읽기)
- 검색 JOIN 조건: `src/obsidian_sync/repositories/search.py:83-88`, `:121-125` (`vf.content_hash = kc.content_hash`)

**문제**
`reindex_vault`는 대상 파일 목록을 한 번에 조회한 뒤 파일마다 디스크에서 최신 markdown을 다시 읽고 임베딩한다. 그런데 청크의 `content_hash`는 방금 읽은 markdown 해시가 아니라 **목록 조회 시점의 스냅샷** `record.content_hash`를 우선 사용한다(`record.content_hash`가 항상 채워져 있어 뒤의 `sha256_text(markdown)` fallback은 사실상 죽은 코드).

**발생 시나리오**
1. reindex가 파일 A 처리 중(Ollama 왕복으로 초 단위 지연 가능), 동시에 MCP `sync_file`이 파일 A를 수정 → `VaultFile.content_hash` 즉시 갱신.
2. reindex가 청크를 구 해시로 저장 → 검색 JOIN(`vf.content_hash = kc.content_hash`)이 매치되지 않아 **청크가 조용히 검색에서 사라짐**.
3. `index_status='indexed'`로 기록되므로 `changed_only` 재인덱스는 이 파일을 다시 집지 않음. `pending/failed/model_stale_jobs` 어느 신호에도 잡히지 않아 사용자가 인지 불가. `mode=full` 재인덱스만 복구 가능.

**개선 제안**
`content_hash=sha256_text(markdown)`으로 항상 fresh 계산(한 줄 수정). 또는 임베딩 직전 `VaultFile` 행을 재조회해 해시가 변했으면 skip하고 다음 사이클에 재처리.

### 2.2 Go pull: vault 내 심링크 하나로 전체 sync 영구 중단

> 2026-07-14 수정됨: `vaultfs.SafePath`가 거부한 server path는 warning 후
> skip하며, 같은 run의 push 단계에서도 제외한다. 심링크 traversal 자체는 계속
> 금지된다.

**위치** `internal/syncagent/engine/pull.go:62-70`

```go
_, ok := vaultPath(r.cfg.VaultRoot, item.Path, r.cfg.SyncAttachments)
if !ok {
    r.summary.Warnings = append(...)   // 경로 traversal: 경고 후 해당 파일만 skip
    return nil
}
safeDestination, err := vaultfs.SafePath(r.cfg.VaultRoot, item.Path)
if err != nil {
    return fmt.Errorf(...)             // 심링크 컴포넌트: run 전체 하드 실패
}
```

같은 함수 안의 두 "unsafe path" 검사가 다르게 처리된다. `symlink_containment_test.go`로 보아 의도된 설계이지만, 사용자가 vault 안에 개인 정리용 심링크(예: `Projects/current -> Archive/2026/project-x`)를 두면 그 경로 하위로 서버 변경이 들어올 때마다 **매 sync가 전체 실패**한다. 1.1과 결합하면 manifest도 저장되지 않으므로 사용자가 심링크를 제거하기 전까지 다른 정상 변경(push 대기 노트 포함)까지 전부 막힌다.

Python은 같은 상황에서 해당 경로만 경고 후 skip한다(`src/obsidian_sync/sync_agent/engine.py:236-244`) — 구현 간 동작 불일치이기도 하다.

**개선 제안** 심링크 거부를 warning + skip으로 완화하거나, 최소한 에러 메시지에 원인 경로와 조치 방법을 명시.

### 2.3 MCP 도구 에러에서 구조화 정보 전부 소실

> 2026-07-14 수정됨: Streamable MCP tool이 `AppError`를 REST-like structured
> envelope로 반환한다. `status_code`도 error payload에 포함한다.

**위치**
- `src/obsidian_sync/mcp_server.py` — 모든 `@mcp.tool` 함수가 `AppError`를 그대로 전파(파일 전체에 `AppError` catch 없음, 직접 확인)
- FastMCP `tools/base.py` — `Tool.run`이 모든 예외를 `raise ToolError(f"Error executing tool {name}: {e}")`로 래핑

**문제**
REST에서는 `app.add_exception_handler(AppError, ...)`(`app.py:94`)가 `code`(`SYNC_CONFLICT`, `FORBIDDEN` 등), `status_code`, `details`(`server_revision`, `conflicts` 목록)를 envelope로 살려주지만, MCP 도구 호출에서는 이 핸들러에 도달하지 않는다. MCP 클라이언트가 받는 것은 `"Error executing tool sync_file...: Sync conflict detected."` 같은 평문뿐이다.

**영향**
`sync_file` docstring은 409/403 등 구체적 상태를 안내하지만, 에이전트가 "409면 최신본 재조회 후 재시도, 403이면 `base_revision` 방식으로 전환" 같은 분기를 하려면 텍스트 파싱에 의존해야 한다. 충돌 자동 해결에 필요한 `server_revision` 등 구조화 정보도 소실된다.

**개선 제안** 각 도구에서 `AppError`를 catch해 `{"error_code": ..., "message": ..., "details": ...}` dict를 정상 반환값으로 돌려주거나, MCP의 구조화 오류 채널을 사용하도록 재설계.

### 2.4 Go 에이전트: 네트워크 재시도 전무

> 2026-07-14 부분 수정됨: Go client에 안전한 요청(`GET`, device registration
> `POST`) 대상 transient retry를 추가했다. `PUT`/`DELETE`는 request 성공 후
> response 유실 시 재전송 안전성이 보장되지 않아 자동 재시도하지 않는다.

**위치**
- Go: `internal/syncagent/client/client.go:170-182` — 단발 `http.Do`, 재시도 없음
- Python: `src/obsidian_sync/sync_agent/client.py:263-324` — 전이성 오류 화이트리스트, 기본 3회 지수 백오프, `Retry-After` 헤더 존중

와이파이 순간 끊김, 서버 재시작 중 일시적 5xx에도 Go는 즉시 전체 sync 실패(1.1과 결합 시 허위 conflict까지 유발). `docs/sync-agent.md:89-106`의 재시도 설정 섹션은 사실상 Python 전용인데 그렇게 읽히지 않는다.

### 2.5 Go에 watch 모드 부재

`cmd/obsidian-sync-agent/main.go:36-46`의 서브커맨드는 `status`/`sync`/`update`뿐이다. 준실시간 동기화를 원하는 Go 사용자는 cron/launchd 반복 실행을 구성하거나, "호환용"으로만 남겨둔 Python CLI를 uv 개발 환경째 설치해야 한다. 단일 바이너리 배포의 장점이 이 지점에서 무너진다.

---

## 3. MEDIUM — 장기 운영·품질 이슈

### 3.1 리비전/소프트 삭제 GC가 문서상으로만 존재

> 2026-07-14 재검증 정정: soft-delete 파일 정리 스크립트
> `scripts/cleanup_deleted_files.py`는 존재한다. 다만 archive ORM table populate와
> `vault_file_versions` pruning은 여전히 구현되지 않은 운영 과제로 남는다.

- `src/obsidian_sync/api/routes/sync.py:151` docstring: "a cleanup job archives expired soft-deleted files" — `scripts/cleanup_deleted_files.py`가 soft-deleted file 이동 작업을 제공한다.
- `core/config.py:33`의 `sync_soft_delete_retention_days`: `scripts/cleanup_deleted_files.py`에서 사용된다.
- `db/models.py:359-413`의 `ArchivedVaultFile`/`ArchivedKnowledgeChunk`: 테이블만 존재, 데이터를 옮기는 코드 없음

`vault_file_versions`는 리비전마다 마크다운 **전문**을 저장하므로(diff 아님), 장기 운영 시 DB가 무한 증가한다. 정리 배치(CLI 서브커맨드 또는 스케줄 작업)를 구현하거나 docstring을 "구현 예정"으로 정정해야 한다.

### 3.2 재인덱스가 단일 트랜잭션·순차 임베딩으로 처리

**위치** `src/obsidian_sync/services/indexing.py:35-59`, `db/session.py:35-50`

`mode=full` 재인덱스가 HTTP 요청 1건에 바인딩된 하나의 DB 세션/트랜잭션 안에서 전체 파일을 순차 처리한다. 대형 vault에서 (a) 커넥션이 Ollama 지연 시간만큼 장기 점유되어 풀 소모, (b) 루프 밖 예외 발생 시 이미 처리된 수백 파일의 색인 결과까지 전부 롤백, (c) MCP 도구 호출로 실행하면 클라이언트/전송 계층 타임아웃 위험. 배치 단위 중간 commit + `asyncio.gather`(세마포어) 동시 임베딩 + 백그라운드 작업 분리를 권장.

### 3.3 수정 후 자동 재인덱싱 미트리거

`revision_sync.py:305,322`는 `index_status='pending'`만 표시하고 재임베딩을 트리거하지 않는다. `reindex`를 잊으면 새/수정 노트가 무기한 검색에서 누락된다. 다만 검색 JOIN 조건 덕에 **stale 내용이 잘못 반환되지는 않는** fail-safe 설계이고, `search_knowledge` 응답의 `pending_vectorizing_jobs`로 안내는 되어 있다. 능동적 트리거(sync 시 백그라운드 인덱싱 큐)가 있으면 실사용 경험이 크게 개선된다.

### 3.4 한글 토큰 카운트 언더카운트 (추정: 영향 크기 미검증)

**위치** `src/obsidian_sync/domain/chunking.py:11` — `_TOKEN_PATTERN = re.compile(r'\w+|[^\w\s]')`

`\w+`는 공백 없는 한글 음절 연속 구간을 토큰 1개로 센다. 실제 bge-m3(SentencePiece 계열)는 한글을 훨씬 세분화하므로 한글 비중이 높은 노트에서 토큰 수를 크게 과소평가 → 언어별 청크 밀도가 불균일해진다. 추가로 `domain/embeddings.py:36-50`의 메타데이터 헤더(title/type/project/...)가 `MAX_CHUNK_TOKENS=1200` 예산 계산에 포함되지 않는다. bge-m3 컨텍스트 윈도(8192) 여유로 하드 실패 가능성은 낮으나(추정) 청킹 품질 저하는 구조적 이슈.

### 3.5 한글 lexical 검색 품질 (추정)

`to_tsvector('simple', ...)`(models.py:198-211, alembic 0006/0008)은 형태소 분석을 하지 않아 "임베딩"/"임베딩은"/"임베딩을"이 서로 다른 렉심으로 취급된다. 하이브리드 검색의 vector leg가 의미적으로 보완하지만, 정확한 키워드·고유명사 매칭이라는 lexical leg의 이점이 한국어 콘텐츠에서는 제한적이다.

### 3.6 Python 스캐너의 심링크 미처리 + Go/Python 스캔 대상 불일치 (추정)

- `src/obsidian_sync/sync_agent/scanner.py:53` — `full.stat()`이 심링크를 따라가며 lstat 가드가 없어, 깨진 심링크가 있으면 `FileNotFoundError`가 `cli.py`의 `ConfigError`/`SyncError` 핸들러에 잡히지 않고 트레이스백 노출 가능(추정).
- Go 스캐너(`internal/syncagent/scanner/scanner.go:44-46`)는 심링크를 전부 skip, Python은 대상 파일 내용을 따라가 push — **에이전트에 따라 동기화 대상 파일 집합이 달라진다.** 심링크 컨테인먼트 방식도 다름(Go: 컴포넌트 발견 즉시 거부 / Python: `resolve()` 후 최종 경로만 검사 → vault 내부 심링크 허용).

### 3.7 대소문자만 다른 경로 처리 부재 (추정)

macOS(APFS)/Windows(NTFS)는 기본 case-insensitive다. 서버가 `Notes.md`/`notes.md`를 별개로 관리하면 pull 시 로컬에서 하나의 파일에 번갈아 덮어써지고, 다음 스캔에서 한쪽이 "로컬 삭제"로 오판되어 서버로 삭제가 전파될 위험이 있다. 케이스 충돌 감지(정규화 키 그룹화) 또는 문서 경고 필요.

### 3.8 Ollama 재시도 부재

`clients/ollama.py:34-43, 71-80` — 일시적 네트워크 오류에도 즉시 실패. 파일 단위 격리(`failed_files` 기록)는 되어 있어 폭발 반경은 제한적이지만, 매번 수동 `reindex_vault(mode=changed_only)` 재시도가 필요하다.

### 3.9 서비스 계층의 중간 commit 예외 케이스

`services/revision_sync.py:559-562`(`_raise_sync_conflict`)는 충돌 레코드 보존을 위해 명시적으로 `session.commit()` 후 예외를 던진다. 근거 주석이 있는 의도된 설계지만, "리포지토리는 커밋하지 않는다"는 AGENTS.md 불변식의 유일한 예외이므로 규칙 문서에 명문화하거나 별도 세션으로 분리하는 편이 안전하다.

---

## 4. LOW

| 항목 | 위치 | 요지 |
|---|---|---|
| conflict 파일명 초 단위 타임스탬프 | `internal/syncagent/conflict/conflict.go:35-39`, Python `conflict.py:16-19` | 같은 초 내 다른 revision 충돌 시 `os.Rename`으로 조용히 덮어씀. 밀리초 정밀도 또는 revision 포함 권장 |
| `/health`가 관리자 토큰 요구 | `api/router.py:19-21` | LB/오케스트레이터 헬스체크에 불편. 무인증 `/health/live` 분리 권장 |
| 관리자 토큰 미설정 시 500 vs 401 구분 | `core/auth.py:67-84` | 호출자가 "설정 누락"과 "토큰 오류"를 구분 가능 — 경미한 정보 노출. 둘 다 401로 통일 권장 |
| 대형 vault 동기화 진행률 표시 없음 | Go `engine.go`/`push.go`/`pull.go` | 엔진에 로깅 호출 자체가 없음. 초기 동기화 시 멈춤/진행 구분 불가 |
| Go `init` 스캐폴딩 명령 부재 | `cmd/obsidian-sync-agent/main.go:36-46` | config.json을 손으로 작성해야 함. 누락 시 에러 메시지 자체는 친절함 |
| conflict 발생 시 CLI 안내 부재 | `main.go:237-260` (`printSummary`) | exit 1 + 경로 나열만. 문서의 4단계 해결 절차를 한 줄 안내로 출력 권장 |
| 검색 결과 `content` 무절단 | `schemas/search.py:37-71` | top_k=10 × 최대 ~1200토큰 청크가 한 응답에 포함 가능 — MCP 토큰 낭비 |
| dry-run과 실제 실행 결과 차이 | `engine.go:107-132` | dry-run은 커서를 이동하지 않으므로 이후 실제 sync의 변경 목록이 달라질 수 있음(파일 시스템 부작용은 없음 — 테스트로 검증됨) |
| 파일 쓰기 OSError가 공통 에러 envelope로 변환 안 됨 | `services/storage.py:76-96` | 디스크 풀/권한 오류 시 기본 500 응답(정합성은 유지). `AppError` 변환 권장 |
| frontmatter 커스텀 파서 엣지 케이스 (추정) | `domain/frontmatter.py:109-138` | 값 내 콤마, 2-space 외 들여쓰기, 중첩 매핑에 취약. 실패 시 파일 단위 격리는 됨 |
| rerank JSON 파싱 강건성 (추정) | `domain/rerank.py:72-94` | 모델이 지시를 어기면 잘못된 배열 파싱 가능. 실패 시 원래 순서 폴백은 있음 |
| 검색 페이지네이션 부재 | `domain/search.py:13-14` (`MAX_TOP_K=10`) | 상위 10개 초과 결과 확인 불가 (토큰 절약 목적의 의도적 설계로 보임) |

---

## 5. 문제 없음으로 확인된 항목

교차 검증 과정에서 우려 대비 견고함이 확인된 부분:

- **동시 push/리비전 경합**: `SyncRepository.get_file_for_update`의 `SELECT ... FOR UPDATE`(repositories/sync.py:27-34) + 원자적 `UPDATE ... RETURNING` 리비전 증가(:97-114) + `UniqueConstraint('vault_id','source_path')` + `_commit_staged_file`의 DB/스토리지 동시 롤백(revision_sync.py:575-601). 별도 uvicorn 프로세스 + 8스레드 통합 테스트(`tests/test_sync_concurrency.py`)로 "정확히 1승/나머지 409" 검증됨.
- **경로 traversal 방어**: `domain/paths.py`가 `..`, 절대경로, 드라이브 문자, NUL, 백슬래시, Windows 예약어, 255바이트 초과 세그먼트를 차단하고 `resolve()` 기반 재검증.
- **SQL 인젝션**: `repositories/search.py`의 raw SQL 전부 바인드 파라미터 사용.
- **토큰 관리**: SHA-256 해시 저장, 생성 시 1회 평문 노출, `secrets.compare_digest` 비교, `allow_overwrite` 권한 세분화.
- **삭제 전파**: `delete_file`이 청크를 즉시 삭제(revision_sync.py:393) — 삭제된 노트가 검색에 남지 않음.
- **HNSW 인덱스**: `alembic/versions/20260701_0003_add_chunks_embedding_hnsw.py`에 존재. 코사인 유사도 환산(`1 - (embedding <=> ...)`)도 정확.
- **임베딩 차원 검증**: `services/indexing.py:159-168`에서 명시적 검사 후 파일 단위 격리.
- **변경 감지**: mtime이 아닌 SHA-256 content hash 기반 — 타임존/클럭 스큐 오탐 없음.
- **Go 업데이터**: 다이제스트 상수 시간 비교, draft/prerelease 거부, 아카이브 내용 검증, 원자적 rename 교체, 사용자 확인 프롬프트.
- **MCP 도구 docstring**: `pending/failed/model_stale_jobs` 의미, RRF와 score 비단조성, per-source cap 등 에이전트 관점에서 모범적 수준.
- **restore/복원 경합**: 행 잠금으로 직렬화, 이중 복원은 409로 정상 종료.

---

## 6. 권장 조치 순서

1. **stale `content_hash` 수정** (§2.1) — `indexing.py:187` 한 줄. 영구 검색 누락 경로 제거.
2. **manifest 증분 저장** (§1.1) — Go/Python 공통. 가장 사용자 체감이 큰 허위 conflict 제거. "중단 후 재실행" 회귀 테스트 동반.
3. **Go 심링크 skip 완화 + HTTP 재시도 추가** (§2.2, §2.4) — 프로덕션 에이전트 견고성.
4. **MCP 구조화 에러 응답** (§2.3) — MCP 중심 사용이라면 에이전트 자동화 품질에 직결.
5. **리비전 GC 구현** (§3.1) — 장기 운영 전 필수. docstring 정정 포함.
6. **Go/Python 기능 격차 해소 또는 문서화** (§1.2, §2.5, §3.6) — conflict_policy, watch, 심링크/재시도 동작 차이.
