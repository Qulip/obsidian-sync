# 서비스 종합 테스트 계획

## 목적과 범위

FastAPI REST API, REST 형태 MCP API, streamable HTTP MCP 도구, Go 동기화
에이전트, Python 호환 에이전트, 운영 스크립트 및 knowledge-management
스킬 스크립트가 실제 프로세스에서 정상 동작하는지 확인한다. 벡터 검색은
전용 성능 vault에 임의 Markdown 문서를 대량 등록해 색인과 검색을 분리 측정한다.

## 격리와 사전 조건

- PostgreSQL/pgvector의 전용 `obsidian_sync_test` 데이터베이스와 임시 storage
  root를 사용한다. 운영 vault와 운영 데이터베이스는 사용하지 않는다.
- API는 테스트 전용 포트에서 Uvicorn으로 실행하고, 관리자 토큰과 DB bearer
  token을 런타임에 생성한다.
- 색인과 검색은 실제 PostgreSQL, 실제 API 표면, 설정된 Ollama 모델을 사용한다.
- 테스트 데이터는 종료 시 전용 데이터베이스 재생성 및 임시 storage 제거로
  정리한다.

## 테스트 매트릭스

| 표면 | 시나리오 | 통과 기준 |
|---|---|---|
| REST API | 18개 경로: health, 토큰 생성·조회·폐기, vault 생성·조회, 파일 create/read/update/delete/restore, device/changes/status, 재색인, 검색/feedback/log | 모든 엔드포인트가 의도한 상태 코드·응답 envelope·상태 전이를 반환 |
| REST MCP | 7개 경로: vault 목록, 파일 저장·충돌·overwrite, 재색인, 검색/feedback, note, sync status | bearer 인증을 적용하고 정상 workflow를 완료 |
| MCP protocol | `initialize`, `tools/list`, 7개 `tools/call` | 정확히 7개 도구가 노출되고 각 도구 호출이 성공 envelope를 반환 |
| Go CLI | build, `--help`, 잘못된 명령, `status`, 최초 sync, 수정 sync, `--dry-run` | 도움말/오류 종료 코드와 서버·로컬 manifest 상태가 일치 |
| Python 호환 CLI | `--help`, 최소 dry-run | 호환 명령 표면이 기동하고 유효하지 않은 입력이 명확히 실패 |
| 운영·스킬 스크립트 | `scripts/{backup,cleanup,eval_search,list_vaults,save_knowledge}.py`, `SKILLS/knowledge-management/scripts/{list_vaults,save_knowledge}.py`의 모든 `--help` 및 실제 list/save/backup/cleanup/eval | save가 indexable frontmatter를 만들고 파일별 재색인 실패는 경고로 표시하되, HTTP 요청 실패는 비정상 종료 |

## 벡터 검색 성능 시나리오

1. `perf-<timestamp>` 전용 vault에 필수 frontmatter를 포함한 임의 Markdown
   1,000개를 public write API로 등록한다. 각 문서는 고유 식별자·태그·한글/영문
   본문을 가지며 약 한 청크가 되도록 만든다.
2. `changed_only` 재색인을 한 번 실행하고 문서/청크 수, 실패 수, 총 시간,
   docs/s와 chunks/s를 기록한다. 재색인 요청 timeout은 `max(600초, 문서 수 × 30초)`로
   설정한다. 클라이언트 timeout이 나도 즉시 재실행하지 않고 sync status를 조회해
   서버 작업 완료 여부를 확인한다. 실패가 있으면 부하 측정을 중단하고 원인을 수정한다.
3. 20회 warm-up 뒤 100개 검색(의미 질의, 정확 키워드, metadata filter, miss)을
   순차 실행하고 HTTP p50/p95/p99, 오류 수, `index_fresh`, Recall@5를 기록한다.
4. 같은 질의를 동시성 5로 실행해 QPS와 p95를 기록한다. 기본 hybrid 검색 결과를
   기록하고, 순수 vector/lexical 비교가 필요하면 별도 설정 실행으로 분리해 남긴다.
5. 대표 vector SQL에 `EXPLAIN (ANALYZE, BUFFERS)`를 실행해 partial HNSW cosine
   index 사용 여부를 확인한다. lexical GIN plan은 순수 lexical 비교 실행 시 확인한다.

성능 수치는 실행 환경에 종속되므로 절대 임계값 대신 오류 0, 색인 신선도 true,
기대 문서 Recall@5 100%, 그리고 측정 원시값을 결과로 기록한다.

## 회귀 게이트와 결함 처리

- Python: `uv run ruff check . && uv run mypy && uv run pytest -q`
- Go: `go test ./... && go vet ./... && make build-agent`
- 재현 가능한 결함은 실패 테스트를 먼저 추가하고 최소 변경으로 수정한 뒤,
  해당 사용자 표면에서 다시 확인한다.

## 실행 결과 (2026-07-13)

- 실제 Uvicorn 프로세스에서 REST API 18개, REST MCP 7개, Streamable HTTP MCP의
  `initialize`/`tools/list` 및 7개 도구 호출을 모두 성공시켰다.
- Go agent는 build, help, 잘못된 명령(exit 2), status, dry-run 및 실제 최초 sync를
  수행했다. 실제 sync 결과는 `pulled=1`, `pushed=1`, `conflicts=0`이었다. Python
  호환 CLI의 help, sync, status 표면도 실행했다.
- 운영 스크립트와 skill fallback 스크립트의 help 및 실제 list/save를 실행했고,
  backup archive 검증, cleanup, 임시 golden set의 `eval_search`도 실행했다.
- 결함: 두 `save_knowledge.py`가 필수 frontmatter를 누락해 업로드는 성공하지만
  재색인이 실패했다. 두 사본이 완전한 indexable frontmatter를 생성하도록 수정했다.
  파일별 재색인 실패는 skill 계약대로 경고만 출력하고, 재색인 HTTP 요청 실패는 exit 1이
  되도록 회귀 테스트를 추가했다.
- `perf-1000` vault에 1,000개 문서와 1,000개 chunk를 등록했다. full reindex는
  198.56초, 실패 0건이었다. 20회 warm-up 뒤 100개 정확 식별자 질의의 Recall@5는
  1.00이었다. 순차 실행은 QPS 5.20, p50/p95/p99 191.78/205.48/211.76 ms,
  동시성 5는 QPS 5.44, p50/p95/p99 898.93/1466.20/1651.08 ms였다.
- PostgreSQL의 vector `EXPLAIN (ANALYZE, BUFFERS)`는 `idx_knowledge_chunks_embedding_hnsw`
  index scan을 사용했고 실행 시간은 4.012 ms였다. API 성능 수치는 기본 hybrid 검색
  경로의 end-to-end 결과다.
