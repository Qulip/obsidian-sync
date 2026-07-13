# 문서 임베딩 및 MCP 벡터 검색 품질 분석

> 작성일: 2026-07-12
> 대상: `obsidian-sync`의 Markdown reindex, `knowledge_chunks`, pgvector 검색, MCP `search_knowledge`
> 분석 기준: 현재 코드의 `ReindexService`, `chunk_markdown`, `parse_frontmatter`, `format_chunk_embedding_input`, `KnowledgeSearchService`, `SearchRepository.search_chunks`

---

## 요약 결론

현재 벡터 검색은 개인 지식 저장소의 1차 semantic recall 용도로는 동작 가능한 baseline이다. `bge-m3` 1024차원 임베딩, heading 기반 chunking, frontmatter metadata를 포함한 embedding input, pgvector cosine 검색, vault/project/domain/tags 필터, search log가 있다.

다만 MCP가 이 검색 결과를 근거로 답변하는 용도라면 **정확성이 충분하다고 단정하기 어렵다**. 특히 다음 리스크가 크다.

1. 파일이 수정된 뒤 reindex 전까지 stale chunk가 검색될 수 있다.
2. 검색 쿼리가 `knowledge_chunks.content_hash = vault_files.content_hash` 또는 `vault_files.index_status='indexed'`를 강제하지 않는다.
3. 검색 결과는 순수 vector similarity 순위만 사용하며, keyword match, reranking, score threshold, diversity, 최신성/우선순위 boost가 없다.
4. 검색 품질 평가셋이나 recall/precision 지표가 없다.
5. MCP 응답에서 top result를 그대로 신뢰하면 stale/low-score/부분 chunk 기반 오답 가능성이 있다.

따라서 현재 상태에서는 MCP 검색 결과를 “답변 근거 후보”로 쓰되, 중요한 답변 전에는 `get_note`로 원문 최신본을 재확인하는 운영 규칙이 필요하다. 코드 측면에서는 stale chunk 차단과 평가셋 구축이 최우선이다.

---

## 1. 현재 임베딩 파이프라인

### 1.1 저장 후 reindex 흐름

```text
PUT/RESTORE/MCP sync_file 성공
  -> vault_files.index_status = pending
  -> 명시적 reindex 호출 필요
  -> ReindexService
     -> parse_frontmatter()
     -> chunk_markdown()
     -> format_chunk_embedding_input()
     -> Ollama bge-m3 embed()
     -> knowledge_chunks replace
     -> vault_files.index_status = indexed
```

특징:

- PUT/RESTORE 후 reindex는 자동 실행되지 않는다.
- MCP save workflow도 `sync_file -> reindex_vault` 2단계다.
- `changed_only`는 `vault_files.index_status != 'indexed'` 파일만 처리한다.
- `full`은 `.md` 파일 전체를 다시 처리한다.
- 실패 파일은 `index_status='failed'`가 되고 `index_failure_logs`에 기록된다.

### 1.2 인덱싱 대상

인덱싱 대상은 Markdown 파일만이다. 이미지/PDF 첨부파일은 동기화 대상일 수 있지만 vectorizing 대상은 아니다.

제외 조건:

- `.md`가 아닌 파일
- conflict/sync-conflict 파일
- `.obsidian/`, `.obsidian-sync-agent/`, `.trash/`
- soft-deleted 파일
- frontmatter `vectorize: false`
- frontmatter `status != current`
- frontmatter 누락 또는 필수 필드 오류

### 1.3 Frontmatter 요구사항

`parse_frontmatter()`는 다음 필드를 필수로 요구한다.

```yaml
---
title: ...
type: reference
project: ...
domain: ...
status: current
priority: medium
visibility: personal
tags: [...]
vectorize: true
created: YYYY-MM-DD
updated: YYYY-MM-DD
---
```

장점:

- 검색 필터와 agent hint가 안정적으로 생성된다.
- 품질 낮은 임의 Markdown이 무분별하게 검색에 섞이는 것을 막는다.

한계:

- 자체 YAML subset parser라 nested mapping, 복잡한 YAML, quoted escaping, multiline metadata를 지원하지 않는다.
- frontmatter 형식이 조금만 어긋나도 검색 대상에서 빠진다.
- Obsidian 일반 노트와 호환성이 낮을 수 있다.

### 1.4 Chunking

현재 `chunk_markdown()`은 다음 규칙을 쓴다.

- frontmatter 제거
- H1-H3 heading 기준 section 분리
- 짧은 section은 최소 150 token 기준으로 병합
- 큰 section은 최대 1200 token 기준으로 paragraph 단위 분할
- overlap은 직전 paragraph 기준 최대 100 token
- token 수는 모델 tokenizer가 아니라 정규식 기반 추정치

장점:

- heading path가 보존된다.
- code fence 내부의 `#`는 heading으로 오인하지 않도록 처리한다.
- 작은 section을 병합해 너무 짧은 embedding을 줄인다.

한계:

- `TARGET_MIN_CHUNK_TOKENS`, `TARGET_MAX_CHUNK_TOKENS` 상수는 정의되어 있지만 현재 분할 로직에서는 쓰이지 않는다.
- paragraph 하나가 매우 길면 1200 token을 넘는 chunk가 생길 수 있다.
- H4 이하 heading은 heading path에 반영되지 않는다.
- 인접 heading을 병합할 때 첫 section의 heading path를 유지하므로, 뒤 section의 heading 정보가 약해질 수 있다.
- code block, table, list, callout 등 구조별 최적화는 없다.

### 1.5 Embedding input

각 chunk의 embedding input은 본문만이 아니라 metadata를 포함한다.

```text
Title: ...
Type: ...
Project: ...
Domain: ...
Heading: ...
Tags: ...

Content:
...
```

장점:

- 짧은 chunk도 title/project/domain/tags 맥락을 갖는다.
- 프로젝트명이나 도메인 기반 질의에서 recall이 좋아질 수 있다.

리스크:

- 모든 chunk에 같은 metadata가 반복되어, 본문보다 metadata 유사도가 ranking에 과도하게 작용할 수 있다.
- project/domain을 필터로도 쓰고 embedding input에도 넣기 때문에, 필터 없이 검색할 때 특정 프로젝트명이 강한 shortcut이 될 수 있다.
- code identifier, 파일명, 함수명처럼 lexical exact match가 중요한 질의에는 dense embedding만으로 부족하다.

---

## 2. 현재 검색 경로

### 2.1 MCP 검색 흐름

```text
MCP search_knowledge
  -> KnowledgeSearchService.search()
     -> normalize_search_query()
     -> Ollama embed(query)
     -> SearchRepository.search_chunks()
     -> search_logs 기록
     -> top_k 결과 반환
```

REST `/knowledge/search`와 MCP `search_knowledge`는 같은 service/repository 경로를 쓴다.

### 2.2 Ranking

현재 DB 쿼리는 다음과 같은 순수 vector similarity 검색이다.

```sql
SELECT 1 - (kc.embedding <=> :query_embedding) AS score
FROM knowledge_chunks kc
WHERE kc.vault_id = :vault_id
  AND kc.embedding IS NOT NULL
  ...
ORDER BY kc.embedding <=> :query_embedding
LIMIT :top_k
```

필터:

- `vault_id` 필수
- 기본 `status=current`
- optional `types`, `priority`, `visibility`, `tags`, `project`, `domain`

제한:

- 기본 `top_k=5`, 최대 `top_k=10`
- score threshold 없음
- keyword/BM25 없음
- reranker 없음
- MMR/diversity 없음
- recency/priority boost 없음
- 같은 파일에서 여러 chunk가 중복 반환될 수 있음

### 2.3 검색 로그

모든 검색은 `search_logs`에 기록된다.

기록 항목:

- request_id
- token_id
- vault_id
- query
- filters
- top_k
- result_count
- latency_ms
- client_ip/user_agent

한계:

- 사용자가 어떤 결과를 채택했는지 기록하지 않는다.
- 정답 문서/정답 chunk를 저장하지 않는다.
- 실패 검색, 무관한 top result, user feedback을 수집하지 않는다.

---

## 3. 정확도 리스크

### P0. Stale chunk 검색 가능성

가장 큰 정확도 리스크다.

파일이 수정되면 `vault_files.index_status='pending'`이 되지만, 기존 `knowledge_chunks`는 reindex 전까지 남아 있다. 현재 검색 쿼리는 `vault_files`와 join하지만 다음 조건을 강제하지 않는다.

- `kc.content_hash = vf.content_hash`
- `vf.index_status = 'indexed'`
- `vf.deleted = false`

따라서 reindex 전에는 구버전 chunk content가 검색될 수 있다. 더 위험한 점은 검색 결과의 `revision`과 `updated_at`은 join된 최신 `vault_files` 값으로 표시될 수 있다는 것이다. 즉, **내용은 구버전인데 revision은 최신처럼 보이는 결과**가 생길 수 있다.

MCP가 이 결과를 근거로 답하면 최신 문서와 다른 답을 할 수 있다.

권장 수정:

```sql
JOIN vault_files vf
  ON vf.vault_id = kc.vault_id
 AND vf.source_path = kc.source_path
 AND vf.content_hash = kc.content_hash
WHERE vf.deleted = false
  AND vf.index_status = 'indexed'
```

선택지:

- 정확도 우선: stale chunk는 검색에서 제외한다.
- recall 우선: stale 결과를 반환하되 `stale=true`, `indexed_content_hash`, `current_content_hash`를 응답에 표시한다.

MCP 용도라면 정확도 우선이 적합하다.

### P0. Sync 후 reindex 미실행 문제

MCP save workflow는 `sync_file -> reindex_vault`이다. `sync_file`만 성공하고 `reindex_vault`를 호출하지 않으면 검색 결과에는 반영되지 않는다.

현재 `get_sync_status`에 `pending_vectorizing_jobs`가 있으므로, MCP client는 검색 전 상태를 확인할 수 있다. 하지만 `search_knowledge` 자체는 pending 상태를 경고하지 않는다.

개선안:

- `search_knowledge` 응답에 `pending_vectorizing_jobs` 또는 `index_freshness`를 포함한다.
- MCP tool description에 “save 후 반드시 reindex”뿐 아니라 “검색 전 pending jobs가 있으면 결과가 stale할 수 있음”을 명시한다.
- sync 성공 후 자동 reindex queue를 도입한다.

### P1. 순수 vector ranking

현재는 dense embedding similarity만으로 top_k를 결정한다. 의미 유사 질의에는 좋지만 다음 질의에는 약할 수 있다.

- 정확한 함수명, 클래스명, 파일명
- 에러 메시지
- CLI command
- config key/env var
- 약어, 제품명, 고유명사
- “이전에 결정한 X의 이유”처럼 특정 decision note를 찾는 질의

개선안:

- PostgreSQL full-text search 또는 trigram search를 추가한다.
- dense vector top 50 + lexical top 50을 후보로 모은 뒤 rerank한다.
- 코드/명령어/에러 메시지는 exact/lexical match 점수를 더 크게 준다.

### P1. Reranker 없음

embedding model의 1차 nearest neighbor만으로 최종 순위를 확정한다. MCP 답변 품질을 높이려면 top candidate를 cross-encoder 또는 LLM judge로 재정렬하는 것이 효과적이다.

현실적인 단계:

1. vector `top_k * 5` overfetch
2. same source diversity 적용
3. lightweight reranker 추가
4. 최종 top_k 반환

로컬 우선이면 Ollama에서 rerank 모델을 쓸 수 있는지 검토하고, 어렵다면 LLM 기반 rerank를 optional로 둔다.

### P1. Score threshold 없음

현재는 어떤 질의든 top_k개를 반환한다. 관련 문서가 없어도 가장 가까운 chunk가 반환되므로, MCP가 “근거 없음” 대신 엉뚱한 근거를 사용할 수 있다.

개선안:

- 최소 score threshold를 도입한다.
- threshold 미만이면 `results=[]` 또는 `low_confidence=true`를 반환한다.
- threshold는 평가셋으로 보정해야 한다. 초기값은 보수적으로 두고 search log로 조정한다.

### P1. 평가셋 없음

현재 코드와 테스트에는 검색 품질 평가가 없다. HNSW index migration 테스트는 있지만, retrieval accuracy 테스트는 없다.

필요한 평가셋:

```yaml
- query: "FastAPI dependency override 패턴"
  vault_id: personal-main
  expected_sources:
    - "backend/fastapi/testing.md"
  expected_headings:
    - ["Testing", "Dependency overrides"]
  must_contain:
    - "dependency_overrides"
```

지표:

- Recall@5
- MRR@10
- nDCG@10
- no-result precision
- stale-result rate
- duplicated-source rate

### P2. Chunking 튜닝 여지

현재 chunking은 단순하고 실용적이지만, 검색 정확도 최적화는 아직 덜 되어 있다.

개선 후보:

- 모델 tokenizer 기반 token count
- H4-H6 heading 반영
- code block/table/list/callout 별도 처리
- 긴 paragraph 강제 분할
- chunk마다 parent heading context를 content 앞에 명시적으로 포함
- adjacent small sections 병합 시 heading path 합성 또는 representative heading 보존
- 중복 overlap chunk의 검색 중복 제거

### P2. Metadata embedding과 filtering 역할 분리

현재 project/domain/tags는 embedding input에도 들어가고 metadata filter에도 들어간다. 이 전략은 recall에 도움되지만, metadata가 ranking을 과도하게 지배할 수 있다.

실험할 옵션:

- content-only embedding과 metadata+content embedding 비교
- query에 project/domain filter가 있으면 embedding input에서는 metadata weight를 줄임
- title/heading만 embedding input에 포함하고 project/domain은 filter/boost로 처리

### P2. MCP 결과 소비 규칙 부족

검색 결과는 `answer_context.recommended_action`으로 “source_path, heading_path, agent_hint를 먼저 보라”고 안내하지만, MCP client가 반드시 원문 최신본을 확인하도록 강제하지 않는다.

권장 MCP 사용 규칙:

1. `search_knowledge`로 후보 검색
2. score가 낮거나 pending jobs가 있으면 답변에 불확실성을 표시
3. 중요한 답변은 `get_note`로 최신 파일 내용을 확인
4. 답변에는 source_path와 heading_path를 근거로 표시

---

## 4. 개선 우선순위

### P0. 검색에서 stale chunk 차단

`SearchRepository.search_chunks()`에 `vault_files` 최신 상태 조건을 추가한다.

권장 조건:

- `vf.deleted = false`
- `vf.index_status = 'indexed'`
- `kc.content_hash = vf.content_hash`
- 가능하면 `vf.status = 'current'`

이 변경은 MCP 정확도에 직접적이다.

### P0. 검색 신선도 표시

`KnowledgeSearchResponse`에 다음 중 일부를 추가한다.

- `pending_vectorizing_jobs`
- `index_fresh: bool`
- `stale_results_filtered_count`
- `low_confidence: bool`

적어도 MCP tool description과 `answer_context`에는 pending reindex 시 stale 가능성을 알려야 한다.

### P1. 평가셋과 회귀 테스트 구축

작은 golden set부터 시작한다.

- 20-50개 실제 질문
- expected source/heading
- no-result expected query
- exact keyword query
- Korean/English mixed query
- code identifier query

CI에서는 Ollama를 매번 띄우기 어렵다면 embedding fixture를 사용해 repository/ranking을 테스트하고, 별도 local evaluation command에서 실제 bge-m3를 사용한다.

### P1. Hybrid search

pgvector만으로 부족한 exact match 문제를 보완한다.

후보 생성:

- vector top 50
- full-text/trigram top 50
- source_path/title/heading/tag exact boost

최종 ranking:

- weighted score
- optional reranker
- same source diversity

### P1. Score threshold와 no-result 처리

MCP 검색에서 관련 없는 chunk를 반환하는 문제를 줄인다.

- `min_score`를 설정 가능하게 한다.
- 기본 threshold는 평가셋으로 보정한다.
- threshold 미만 결과만 있으면 “관련 근거 없음”을 반환한다.

### P2. Rerank 옵션

정확도가 중요한 질의에서는 latency를 조금 더 쓰더라도 rerank가 유리하다.

구조:

```text
embed query
  -> vector overfetch top 30-50
  -> lexical 후보 merge
  -> rerank
  -> top_k
```

### P2. Chunking 개선

- tokenizer 기반 token count로 교체
- 긴 paragraph/code block 분할
- H4-H6 heading path 지원
- chunk content 앞에 heading breadcrumb 추가
- table/list/callout 단위 보존

### P2. Search log를 평가 데이터로 전환

검색 로그에 다음 필드를 추가하거나 별도 feedback endpoint를 만든다.

- selected_source_path
- selected_chunk_index
- result_helpful
- expected_missing
- user_feedback

이 데이터로 threshold/ranking 튜닝을 할 수 있다.

---

## 5. MCP 사용 시 권장 운영 규칙

현재 코드만 기준으로는 MCP 벡터 검색 결과를 최종 사실로 바로 쓰기보다 다음 규칙을 권장한다.

1. `get_sync_status`로 `pending_vectorizing_jobs`를 확인한다.
2. pending이 있으면 `reindex_vault(mode=changed_only)`를 먼저 실행한다.
3. `search_knowledge`는 후보 검색으로 사용한다.
4. top result의 score가 낮거나 질의가 exact keyword 성격이면, 결과를 그대로 신뢰하지 않는다.
5. 중요한 답변은 `get_note`로 최신 원문을 확인한다.
6. 답변에는 `source_path`와 `heading_path`를 함께 남긴다.

---

## 6. 판단

현재 임베딩/검색 구현은 다음 조건에서는 충분히 유용하다.

- 개인 노트 수가 크지 않다.
- 문서 frontmatter가 일관적이다.
- 사용자가 project/domain/tags 필터를 잘 준다.
- 검색 결과를 사람이 또는 MCP agent가 다시 확인한다.
- sync 후 reindex를 빠뜨리지 않는다.

하지만 다음 수준을 기대하면 개선이 필요하다.

- MCP가 자동으로 정확한 근거를 찾아 답변해야 한다.
- 최신 문서 반영이 중요하다.
- 함수명/에러 메시지/설정 키 같은 exact query가 많다.
- 관련 문서가 없을 때 “없다”고 말해야 한다.
- 검색 품질을 회귀 없이 관리해야 한다.

최우선은 **stale chunk 차단**과 **검색 품질 평가셋 구축**이다. 그 다음 hybrid search, threshold, rerank를 단계적으로 넣는 것이 가장 효율적이다.

---

## 7. 후속 코드 재검증 (2026-07-12)

### 7.1 재검증 결론

기존 분석에서 제시한 P0/P1 개선안은 대부분 현재 코드에 올바르게 반영됐다. 특히 MCP가 오래된 chunk를 최신 문서처럼 인용하던 가장 큰 위험은 해소됐다. 다만 현재 상태를 “MCP가 자동으로 답변 근거를 확정해도 충분히 정확하다”라고 평가할 수는 없다. 실제 vault 기반 golden set의 측정값이 없고, 일부 응답 상태와 ranking 의미가 MCP 소비자에게 오해를 줄 수 있기 때문이다.

현재 구현은 개인 지식 저장소에서 **후보를 찾고 원문을 확인하는 retrieval layer**로는 역할에 잘 맞는다. 반대로 검색 결과만으로 사실을 단정하는 answer engine으로 사용하려면 아래 P1 항목을 보완하고 실제 corpus로 품질 기준을 정해야 한다.

### 7.2 기존 권고의 반영 여부

| 기존 권고 | 구현 상태 | 코드 확인 결과 |
| --- | --- | --- |
| stale chunk 차단 | 반영 완료 | `SearchRepository.search_chunks()`와 lexical 쿼리는 `vault_files`를 `vault_id`, `source_path`, `content_hash`로 join하고 `vf.deleted = false`, `vf.index_status = 'indexed'`를 강제한다. 수정 후 재색인 전의 기존 chunk는 반환되지 않는다. |
| 검색 신선도 표기 | 부분 반영 | 응답에 `pending_vectorizing_jobs`, `index_fresh`가 있고 MCP tool 설명도 재색인을 안내한다. 다만 `failed` 파일은 집계하지 않아 아래 7.3의 문제가 남는다. |
| hybrid search | 반영 완료 | 기본 활성화(`search_hybrid_enabled=True`)이며 기본 후보 한도 50개로 pgvector와 PostgreSQL FTS 후보를 각각 수집해 RRF로 합친다. FTS는 GIN index를 가진 `title + content` `tsvector`를 사용하고, 두 경로에 같은 metadata filter를 적용한다. |
| score threshold/no-result | 부분 반영 | `min_score` 검증·응답·threshold로 모두 제거된 경우의 `low_confidence`가 구현됐다. 기본값은 `0.0`이므로 운영 환경에서 별도 설정/요청이 없으면 threshold는 적용되지 않는다. 무후보 처리도 보완이 필요하다. |
| optional rerank | 반영 완료 | 기본 비활성화이며 설정된 LLM이 상위 후보를 listwise rerank한다. 호출/파싱 실패 시 원래 RRF 순서로 안전하게 복귀한다. |
| chunking 개선 | 반영 완료 | H1-H6 heading path, 긴 paragraph의 강제 분할, 병합 section의 공통 heading path가 구현됐다. 단, token 수는 여전히 모델 tokenizer가 아닌 정규식 추정치다. |
| 평가·피드백 기반 | 기반만 반영 | deterministic ranking 회귀 테스트, local evaluation harness, feedback endpoint는 추가됐다. 커밋된 golden query는 형식 예시 3건뿐이라 실제 정확도 지표는 아직 없다. |

### 7.3 확인된 잔여 정확도/운영 리스크

#### P1. `index_fresh`가 실패한 인덱싱을 fresh로 표시할 수 있음

`count_pending_reindex()`는 `index_status='pending'`만 센다. 반면 `ReindexService`는 frontmatter/embedding/read 실패 시 상태를 `failed`로 바꾸며, 검색 SQL은 `indexed` 파일만 반환한다. 따라서 pending이 0이고 failed 파일이 있는 vault는 `index_fresh=true`로 응답하지만, 해당 파일은 검색 결과에서 빠진 불완전한 index다.

이는 stale content를 노출하지 않는다는 점에서는 올바르지만, MCP가 “complete/fresh”로 해석하면 안 된다. `failed_vectorizing_jobs`를 별도로 반환하거나, `index_fresh`를 `pending == 0 and failed == 0`으로 계산하고 실패 경로를 `answer_context`에 명시하는 것이 적절하다. 이 상태를 다루는 회귀 테스트도 필요하다.

#### P1. 후보가 전혀 없을 때 응답 안내가 실제 결과와 모순됨

`low_confidence`는 “후보는 있었지만 min_score가 모두 제거된 경우”에만 `true`다. vector/lexical 후보가 모두 없으면 `results=[]`, `low_confidence=false`가 되고, pending도 없을 때 `answer_context`는 “matching chunks”를 반환했다고 안내한다. 이는 MCP가 근거 없음과 정상 매칭을 구별하는 데 부정확하다.

`results`가 비어 있으면 원인과 무관하게 “supporting evidence 없음”을 반환하고, threshold로 제거됐는지/애초 후보가 없었는지는 별도 필드(예: `no_candidates`)로 구분하는 편이 안전하다.

#### P1. RRF 순위와 반환 `score`의 의미가 다르고, threshold가 lexical recall을 제거할 수 있음

hybrid 모드의 최종 순위는 RRF지만 응답의 `score`는 cosine similarity다. 따라서 1위 결과의 score가 2위보다 낮을 수 있으며, 이 값으로 RRF 최종 관련도를 해석하면 안 된다. 현재 코드 주석에는 이 사실이 명시돼 있으나 MCP 응답 schema/tool 설명에는 ranking score의 의미가 드러나지 않는다.

더 중요한 점은 `min_score`가 RRF 이전의 vector score 기준으로 적용된다는 것이다. 정확한 함수명·오류 메시지처럼 FTS에서만 강하게 잡힌 문서는 cosine score가 낮아 threshold에서 제거될 수 있다. 기본 threshold가 꺼져 있을 때는 문제가 없지만, no-result 정확도를 높이기 위해 threshold를 활성화할 때는 lexical match가 있는 결과의 정책을 따로 정해야 한다. 예를 들어 vector-only 후보에만 threshold를 적용하거나, RRF/lexical 신호를 포함한 별도 confidence를 계산해야 한다.

#### P1. 임베딩 모델 교체 시 혼합 embedding을 방지하지 않음

chunk에는 `embedding_model`이 저장되지만 검색 조건은 이를 사용하지 않는다. 또한 `changed_only` 재색인은 파일 변경 상태만 보므로 설정의 embedding model을 바꿔도 기존 indexed 파일은 자동 재임베딩되지 않는다. 모델을 교체한 뒤 full reindex가 진행되는 동안에는 새 query embedding과 이전 모델 chunk embedding이 같은 검색 공간에서 비교될 수 있다.

모델 교체는 vault 단위의 index version/model metadata를 변경하고, full reindex가 끝날 때까지 검색을 막거나 `index_fresh=false`로 표시해야 한다. 최소한 MCP 운영 절차에 “embedding model 변경 후 full reindex 완료 전에는 검색 결과를 신뢰하지 말 것”을 추가해야 한다.

#### P2. lexical 후보의 범위와 결과 다양성은 아직 제한적임

FTS는 `title + content`만 색인한다. `source_path`, tags, project/domain, `agent_hint`는 lexical 대상이 아니므로 파일명·경로·tag 자체를 찾는 exact query는 완전히 보장되지 않는다. 또한 같은 문서의 인접/overlap chunk를 억제하는 MMR 또는 per-source cap이 없어 top_k가 한 파일에 편중될 수 있다.

코드 identifier가 본문에 있는 일반적인 경우에는 hybrid search가 유효하지만, 경로/메타데이터 중심 질의와 여러 근거를 요구하는 MCP 답변에는 exact boost 및 source diversity가 다음 개선 대상으로 적합하다.

#### P2. rerank는 안전하지만 기본 품질 향상으로 검증되지는 않음

rerank는 기본 비활성화이고, 활성화해도 앞선 후보 15개에 대해 각 chunk 앞 500자와 heading/path만 사용한다. 긴 chunk의 핵심 내용이 preview 뒤에 있거나 LLM이 순서를 잘못 판단할 수 있다. 실패 시 fallback하는 설계는 적절하지만, 실제 golden set에서 rerank on/off의 Recall@K·MRR·latency를 비교한 뒤에만 기본 활성화를 검토해야 한다.

### 7.4 MCP 역할에 맞는 사용 판단

`search_knowledge`의 현재 역할은 “개인 노트의 최신 indexed 후보를 hybrid retrieval로 제시”하는 것으로 한정하면 적절하게 구현돼 있다. MCP tool 설명에는 pending 재색인 안내, `request_id`, feedback workflow, optional rerank 여부가 포함되어 있어 agent가 상태를 인식하고 후속 행동을 취할 수 있다.

다만 최종 답변의 근거 확인까지 보장하지는 않는다. 중요하거나 변경 가능성이 높은 답변에서는 다음 순서를 계속 지켜야 한다.

1. `search_knowledge`에서 후보와 `index_fresh`/`low_confidence`를 확인한다.
2. `index_fresh=false` 또는 향후 failed count가 양수면 `reindex_vault(mode=changed_only)`의 결과를 확인한다.
3. 상위 결과의 `source_path`, `heading_path`로 `get_note`를 호출해 최신 원문과 revision을 확인한다.
4. 무후보·low confidence·exact identifier 질의는 “근거를 찾지 못했다”로 처리하거나 필터/질의를 조정한다.

### 7.5 정확성 검증 현황과 다음 우선순위

정적 검증으로 `uv run ruff check .` 및 `uv run mypy`는 통과했다. 검색·chunking 회귀 테스트는 현재 환경에 `.env`의 데이터베이스 URL이 없어 `tests/conftest.py` 초기화 단계에서 실행되지 못했다. 이 테스트들은 실제 PostgreSQL을 요구하며 Ollama embedding은 mock 처리한다. 따라서 이번 재검증에서는 테스트 통과를 주장하지 않고, 코드·테스트·migration을 정적으로 대조했다.

정확도가 충분한지는 실제 vault에서만 판정할 수 있다. 현재 `docs/eval/golden-queries.yaml`은 placeholder이므로, 아래 순서가 가장 효과적이다.

1. 실제 검색 질문 20~50개(한국어/영어, exact identifier, no-result 포함)로 golden set을 교체한다.
2. 현재 hybrid/rerank-off 기준 Recall@5, MRR@10, no-result precision, duplicate-source rate를 기록한다.
3. 7.3의 failed freshness·무후보·model migration 테스트를 추가한다.
4. threshold 정책과 rerank on/off를 동일 golden set으로 비교해 배포 기준을 수치화한다.

이 네 단계가 완료되기 전의 합리적인 결론은 **구현 방향과 stale 차단은 올바르며 retrieval 후보 품질은 개선됐지만, MCP 답변 정확성의 충분성은 아직 실측으로 검증되지 않았다**는 것이다.

### 7.6 7.3 지적 사항 반영 현황 (2026-07-13)

7.3의 잔여 리스크는 아래와 같이 코드에 반영됐다. 전체 검증: `ruff check`/`mypy` 통과, 실제 PostgreSQL 대상 `pytest` 300건 통과.

| 7.3 항목 | 반영 내용 |
| --- | --- |
| failed 인덱싱과 index_fresh | `failed_vectorizing_jobs` 집계 추가, `index_fresh = pending==0 and failed==0 and model_stale==0`, answer_context에 실패 안내 |
| 무후보 응답 모순 | `no_candidates` 필드로 후보 0건과 threshold 전부 제거를 구분, `results=[]`이면 원인 무관 no-evidence 안내 |
| RRF/score 의미와 lexical threshold | 결과별 `matched_by`(vector/lexical/both) 노출, `min_score`는 vector-only 후보에만 적용, score=cosine·순위=RRF임을 schema/MCP 설명에 명시 |
| embedding model 혼합 | 검색 SQL에 `kc.embedding_model = 현재 설정 모델` 조건 추가, `model_stale_jobs` 집계 및 full reindex 안내 |
| lexical 범위·다양성 | `content_tsv`에 source_path(구분자 공백화)·tags(IMMUTABLE 래퍼) 포함 재정의(migration 0008), `search_per_source_limit`(기본 2) per-source cap |

남은 항목은 7.5의 실측 단계(실제 golden set 교체와 지표 기록)와 rerank on/off 비교이며, 이는 실제 vault 데이터가 필요하다.

---

## 8. 수정 사항 최종 코드 검증 (2026-07-13)

### 8.1 검증 결론

7.3에서 지적한 **코드 수정 대상 다섯 항목은 모두 구현 완료**로 확인됐다. 각 보정은 repository/service/schema/MCP tool 설명까지 연결돼 있고, 대응하는 PostgreSQL 통합 테스트도 추가되어 있다. 즉, stale 차단 이후 남았던 상태 표기·무후보 의미·hybrid score/threshold·embedding model 혼합·lexical/diversity 문제는 현재 코드 경로에서 해소됐다.

다만 이는 구현 정확성에 대한 결론이다. 실제 vault corpus에서의 Recall/MRR 및 rerank 효과는 아직 측정되지 않았으므로, 절대적인 검색 품질 수치가 검증된 것은 아니다.

| 7.3 지적 사항 | 최종 확인 결과 |
| --- | --- |
| failed 인덱싱이 fresh로 표시됨 | `count_failed_reindex()`가 `failed_vectorizing_jobs`를 반환하고, `index_fresh`는 pending·failed·model stale가 모두 0일 때만 `true`다. MCP 설명과 `answer_context`도 실패 파일이 검색에서 빠진다는 점을 안내한다. |
| 후보 0건의 응답 모순 | `no_candidates`가 후보 자체가 없음을, `low_confidence`가 threshold로 후보가 제거됐음을 구분한다. 두 경우 모두 `results=[]`이면 supporting evidence가 없다고 안내한다. |
| RRF 순위와 score 혼동 및 lexical threshold 손실 | 결과에 `matched_by`를 노출하고 schema/MCP 설명에서 score=cosine, hybrid rank=RRF를 명시한다. `min_score`는 vector-only 결과에만 적용되어 exact lexical match를 제거하지 않는다. |
| embedding model 혼합 | 두 검색 SQL 모두 `kc.embedding_model = 현재 설정 모델` 조건을 사용한다. 불일치 파일은 `model_stale_jobs`로 집계하고 검색에서 제외하며, MCP는 full reindex를 안내한다. |
| path/tag exact search와 동일 파일 편중 | migration `20260713_0008`이 `content_tsv`를 title/content뿐 아니라 source path 조각과 tags로 확장한다. `search_per_source_limit` 기본값 2의 cap은 rerank 후·top_k 전 적용되어 한 문서의 인접 chunk 편중을 막는다. |

### 8.2 검증 근거

- `uv run ruff check .`: 통과
- `uv run mypy`: 통과 (80 source files)
- `uv run python -m unittest discover -s tests -p 'test_migrations.py'`: 4건 통과
- 각 변경에 대한 통합 테스트 파일 확인: `test_search_freshness.py`, `test_search_no_candidates.py`, `test_search_matched_by.py`, `test_search_model_mismatch.py`, `test_search_diversity.py`

현재 작업환경에는 `.env`가 없어 PostgreSQL 기반 pytest 통합 테스트는 이번 재검증에서 실행할 수 없었다. 따라서 §7.6에 기록된 과거의 PostgreSQL pytest 결과를 본 검증에서 독립적으로 재현한 것은 아니다. 이는 코드 실패가 아니라 현재 검증 환경의 DB 설정 부재이며, DB URL이 준비된 환경에서는 위 통합 테스트 묶음을 다시 실행해 최종 확인하는 것이 좋다.

### 8.3 최종 사용 판단

현재 `search_knowledge`는 최신 content hash와 현재 embedding model에 일치하는 chunk만 대상으로 hybrid retrieval을 수행하고, 결과의 근거 상태와 한계를 MCP 소비자에게 전달한다. 따라서 개인 지식 저장소의 **근거 후보 검색 도구**라는 프로젝트 역할에는 적절하게 구현됐다.

중요한 답변에는 이전과 같이 상위 `source_path`를 `get_note`로 열어 원문·revision을 확인해야 한다. 이후 품질 개선의 기준점은 코드 보정이 아니라 실제 golden set으로 Recall@5, MRR@10, no-result precision, duplicate-source rate 및 rerank on/off latency를 측정하는 일이다.
