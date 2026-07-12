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
