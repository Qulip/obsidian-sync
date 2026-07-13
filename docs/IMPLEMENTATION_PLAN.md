# Obsidian + pgvector 기반 개인 지식 저장소 구현 설계서 v1

> Historical note: 이 문서는 초기 단방향 업로드 설계서입니다. 현재 구현은
> revision 기반 양방향 sync와 `obsisync`를 지원합니다. 최신 운영
> 정책은 `README.md`, `docs/sync-api.md`, `docs/sync-agent.md`,
> `docs/vectorizing.md`를 기준으로 확인하세요.

## 1. 개요

본 시스템은 개인용 지식 저장소이자 AI Coding Agent용 검색 저장소를 구축하기 위한 서비스이다.

업무 PC에서 작성한 Obsidian Vault 문서를 미니PC로 단방향 동기화하고, 미니PC에서는 FastAPI를 통해 문서 저장, 임베딩, 벡터 검색을 제공한다.
벡터 저장소는 별도 VectorDB를 사용하지 않고, 기존 PostgreSQL에 `pgvector` 확장을 추가하여 사용한다.

## 2. 목표

### 2.1 주요 목표

- Obsidian Markdown 문서를 개인 지식 저장소의 원본으로 사용한다.
- 미니PC의 PostgreSQL + pgvector에 문서 chunk embedding을 저장한다.
- FastAPI를 통해 문서 동기화, 인덱싱, 검색 API를 제공한다.
- AI Coding Agent가 FastAPI 검색 API를 호출하여 프로젝트별 지식을 조회할 수 있도록 한다.
- 업무 PC 환경 제약으로 Syncthing/Git 동기화는 사용하지 않고, HTTP API 기반 업로드 방식으로 동기화한다.

### 2.2 비목표

- 양방향 Vault 동기화는 지원하지 않는다.
- 여러 사용자용 권한 관리는 구현하지 않는다.
- 첨부파일의 OCR, PDF 텍스트 추출, 이미지 임베딩은 v1에서 구현하지 않는다.
- reranker는 v1에서 도입하지 않는다.
- LLM 기반 요약/재정렬은 v1에서 구현하지 않는다.

---

## 3. 전체 아키텍처

```text
[업무 PC]
- Obsidian Vault
- Vault upload script
- AI Coding Agent / Skill

        ↓ HTTP API + Bearer Token

[미니PC]
- FastAPI Knowledge Server
- Ollama embedding model: bge-m3
- PostgreSQL + pgvector
- Vault 저장 디렉토리
- Reindex service
```

### 3.1 주요 구성 요소

| 구성 요소             | 역할                                           |
| --------------------- | ---------------------------------------------- |
| Obsidian Vault        | 원본 Markdown 문서 저장소                      |
| Upload Script         | 업무 PC에서 Vault 변경 파일을 FastAPI로 업로드 |
| FastAPI               | 동기화, 저장, 인덱싱, 검색 API 제공            |
| PostgreSQL            | 문서 메타데이터, chunk, 검색 로그 저장         |
| pgvector              | embedding vector 저장 및 유사도 검색           |
| Ollama                | bge-m3 임베딩 모델 실행                        |
| AI Coding Agent Skill | 검색 API 호출 후 작업 context로 활용           |

---

## 4. 확정 정책

## 4.1 Vault 동기화 방향

Vault 동기화는 **업무 PC → 미니PC 단방향**으로 고정한다.

```text
업무 PC Obsidian Vault
        ↓
Upload Script
        ↓
미니PC FastAPI
        ↓
Vault Copy + PostgreSQL
```

미니PC에 저장된 Vault copy는 검색과 백업을 위한 사본이며, 직접 수정하지 않는다.

## 4.2 인증 정책

API 인증은 **Bearer Token** 방식만 사용한다.

```http
Authorization: Bearer <TOKEN>
```

개인용 서비스이므로 token별 권한 분리는 v1에서 구현하지 않는다.
단, 추후 확장을 위해 token 식별자는 로그에 남길 수 있도록 한다.

v1에서는 단일 API token을 환경변수로 설정한다.
`token_id`는 원본 token을 저장하지 않고, token hash의 짧은 식별값으로 기록한다.

## 4.3 DB Vector Dimension

임베딩 모델은 Ollama의 `bge-m3`를 사용한다.
embedding dimension은 1024로 고정한다.

```sql
embedding vector(1024)
```

모델을 변경하는 경우 기존 vector와 dimension 또는 embedding space가 달라질 수 있으므로 전체 재임베딩이 필요하다.

## 4.4 변경 감지 기준

파일 변경 감지는 `content_hash` 기준으로 한다.

```text
같은 vault_id + source_path + content_hash
→ 변경 없음

같은 vault_id + source_path + 다른 content_hash
→ 변경 발생
```

## 4.5 삭제 정책

manifest에 존재하지 않는 파일은 즉시 삭제하지 않고 archive 후보로 판단한다.

`POST /vaults/{vault_id}/sync/manifest`는 archive를 실제 적용하지 않고 `archived_candidates`만 반환한다.
실제 archive 처리는 `POST /vaults/{vault_id}/sync/archive`에서만 수행한다.

삭제 감지 시:

```text
vault_files → archived_vault_files 복사 후 vault_files에서 제거
knowledge_chunks → archived_knowledge_chunks 복사 후 knowledge_chunks에서 제거
검색 대상에서 제외
```

archive 사유는 `archived_reason`에 저장한다.

예:

```text
archived_reason = "missing_in_manifest"
```

## 4.6 Conflict 정책

conflict가 발생하면 API는 실패로 처리한다.
자동 병합이나 자동 덮어쓰기는 하지 않는다.

conflict 예시:

```text
- 서버 파일 hash와 DB hash가 불일치
- 동일 파일에 대해 업로드 중 다른 변경 상태 감지
- manifest 기준 상태와 실제 저장 파일 상태가 다름
- archive 대상인데 동시에 업로드 요청이 들어옴
```

conflict 발생 시 API는 409 Conflict를 반환하고, 사용자가 직접 확인한다.

API별 conflict 기준:

```text
- upload content로 계산한 hash와 요청 hash가 다르면 400 Validation Error
- DB hash와 서버 파일 hash가 다르면 409 Conflict
- manifest 이후 업로드 시점에 같은 path의 DB hash가 바뀌면 409 Conflict
- archive 후보 파일에 업로드가 동시에 들어오면 409 Conflict
```

## 4.7 검색 정책

검색 API는 반드시 `vault_id`를 받아야 한다.
`vault_id` 없는 전체 검색은 허용하지 않는다.

기본 검색 조건:

```text
vault_id = 요청 vault_id
status = current
vectorize = true
```

필요 시 `project`, `domain`, `type`, `priority`, `tags`로 추가 필터링한다.
`visibility=confidential`은 검색 제외 조건이 아니며, 참고용 metadata로만 사용한다.

`top_k` 기본값은 5이고, 최대값은 10이다.
요청값이 없으면 5를 사용하고, 10을 초과하면 validation error로 실패 처리한다.

## 4.8 검색 로그 정책

개인용 서비스이므로 검색 로그는 무조건 저장한다.

저장 대상:

```text
- 요청 IP
- User-Agent
- token 식별자
- vault_id
- 검색 query
- 검색 filter
- top_k
- result_count
- latency_ms
- created_at
```

검색 로그는 기본 30일 이상 보관한다.
백업 정책과 맞춰 최소 1개월 단위로 관리한다.

## 4.9 백업 정책

- 소스코드는 Git에 백업한다.
- Vault copy는 주 단위 tar 파일로 백업한다.
- 백업 파일은 1개월 보관한다.
- PostgreSQL은 별도 `pg_dump` 백업을 권장한다.

---

## 5. 문서 양식

## 5.1 기본 frontmatter

모든 vectorize 대상 문서는 아래 frontmatter를 사용한다.

```yaml
---
title: ''
type: reference
project: ''
domain: ''
status: current
priority: medium
visibility: personal
tags: []
vectorize: true
created: 2026-06-24
updated: 2026-06-24
---
```

frontmatter가 없거나 필수 필드가 누락된 Markdown 문서는 인덱싱 실패로 처리한다.
enum 값이 정의 목록과 다르면 인덱싱 실패로 처리하고 실패 사유를 기록한다.

날짜 필드는 `YYYY-MM-DD` 형식만 허용한다.
YAML parser가 date 객체로 읽을 수 있더라도 저장 및 검증 기준은 ISO date 문자열이다.

## 5.2 type enum

```text
reference       # 구조, 정책, 명령어, 설정처럼 자주 참고하는 문서
rule            # 반드시 지켜야 하는 프로젝트/Agent 규칙
decision        # 의사결정 기록
issue-solution  # 문제, 원인, 해결책, 재발방지 기록
study-note      # 개인 공부 정리본
prompt          # 재사용 가능한 프롬프트
command         # 명령어/스크립트 사용법
checklist       # 작업 전/후 검증 체크리스트
```

## 5.3 status enum

```text
current     # 현재 유효한 문서
draft       # 작성 중, 기본 검색 제외
deprecated  # 과거에는 맞았으나 현재 권장하지 않음
archived    # 보존용, 기본 검색 제외
```

## 5.4 priority enum

```text
high    # 작업 시작 시 우선 참고해야 하는 문서
medium  # 일반 검색 대상
low     # 참고용
```

## 5.5 visibility enum

```text
personal      # 개인 지식
company       # 회사 업무 지식
confidential  # 민감 정보 가능성 있음, 검색 제외 조건은 아님
public        # 외부 공개 가능 지식
```

## 5.6 Obsidian 문법 처리 범위

v1에서는 Obsidian 고유 문법을 별도 graph 구조로 파싱하지 않는다.

```text
- [[Wiki Link]]는 본문 텍스트로 보존하고 embedding 입력에 포함한다.
- [[문서|alias]]는 원문을 보존한다.
- 본문 inline tag(#tag)는 별도 metadata로 추출하지 않고 본문 텍스트로만 사용한다.
- 검색 필터용 tags metadata는 frontmatter의 tags 필드만 사용한다.
- embed 문법(![[file.png]])은 본문 텍스트로 보존하되 첨부파일 내용은 v1에서 임베딩하지 않는다.
```

## 5.7 권장 문서 섹션

Agent 활용성이 높은 문서는 아래 섹션을 권장한다.

```markdown
# 문서 제목

## 요약

## 적용 범위

## 핵심 규칙

## 금지사항

## Agent 지침

## 검증 방법

## 관련 문서
```

---

## 6. Chunking 설계

## 6.1 기본 원칙

Markdown heading 기준으로 chunk를 분리한다.

```text
- H1/H2/H3 heading 경로를 metadata에 저장한다.
- frontmatter는 embedding 대상에서 제외하고 metadata로만 사용한다.
- code block은 설명 문단과 함께 유지한다.
- 너무 짧은 chunk는 인접 chunk와 병합한다.
- 너무 긴 chunk는 문단 단위로 분할한다.
```

## 6.2 Chunk 크기

```text
목표 크기: 600~900 tokens
최소 크기: 150 tokens
최대 크기: 1200 tokens
overlap: 80~120 tokens
```

v1에서는 tokenizer 의존성을 줄이기 위해 Markdown 텍스트 기준의 근사 token counter를 사용한다.
정확한 모델 tokenizer 기반 chunking은 검색 품질 평가 이후 개선 후보로 둔다.

## 6.3 Chunk metadata

각 chunk는 아래 metadata를 가진다.

```text
vault_id
source_path
chunk_index
title
heading_path
project
domain
type
status
priority
visibility
tags
content_hash
embedding_model
created_at
updated_at
```

## 6.4 Embedding 입력 텍스트 구성

embedding 생성 시 chunk content만 사용하지 않고, 검색 품질 향상을 위해 문서 metadata를 함께 포함한다.

```text
Title: {title}
Type: {type}
Project: {project}
Domain: {domain}
Heading: {heading_path}
Tags: {tags}

Content:
{chunk_content}
```

---

## 7. 첨부파일 정책

첨부파일은 동기화 대상에 포함할 수 있지만, v1에서는 임베딩하지 않는다.

## 7.1 동기화 허용 확장자

```text
.md
.png
.jpg
.jpeg
.gif
.webp
.pdf
```

## 7.2 임베딩 대상

```text
.md only
```

v1 파일 업로드 API는 Markdown 텍스트 업로드를 우선 구현한다.
이미지/PDF 첨부파일 동기화는 저장 정책만 유지하고, API payload는 multipart 업로드로 확장할 수 있도록 별도 구현한다.

## 7.3 제외 확장자

```text
.env
.key
.pem
.p12
.zip
.7z
.mp4
.mov
.exe
.dll
.db
.sqlite
```

## 7.4 파일 크기 제한

```text
Markdown: 2MB 이하
Image: 10MB 이하
PDF: 30MB 이하
```

---

## 8. API 설계

## 8.1 공통 규칙

모든 API는 Bearer Token 인증을 요구한다.

```http
Authorization: Bearer <TOKEN>
```

API 응답은 기본적으로 아래 형식을 따른다.

```json
{
  "success": true,
  "data": {},
  "error": null
}
```

실패 응답:

```json
{
  "success": false,
  "data": null,
  "error": {
    "code": "CONFLICT_DETECTED",
    "message": "Conflict detected. Manual verification required.",
    "details": {}
  }
}
```

에러 코드는 중복을 줄이기 위해 아래 목록을 기본으로 사용한다.

```text
UNAUTHORIZED          # 인증 실패 또는 token 누락
FORBIDDEN             # 인증은 됐지만 정책상 허용되지 않는 요청
VALIDATION_ERROR      # 요청 형식, enum, 크기, hash 등 입력값 오류
NOT_FOUND             # vault 또는 파일 등 대상 없음
CONFLICT_DETECTED     # 서버 상태와 요청 상태의 충돌
UNSUPPORTED_FILE_TYPE # 허용하지 않는 확장자 또는 mime type
INDEXING_FAILED       # frontmatter, chunking, embedding, DB 저장 등 인덱싱 실패
EMBEDDING_FAILED      # Ollama embedding 호출 실패
DATABASE_ERROR        # DB 처리 실패
INTERNAL_ERROR        # 분류되지 않은 서버 오류
```

---

## 8.2 Health Check

```http
GET /health
```

응답:

```json
{
  "success": true,
  "data": {
    "status": "ok",
    "postgres": "ok",
    "ollama": "ok",
    "embedding_model": "bge-m3"
  },
  "error": null
}
```

---

## 8.3 Vault 생성

```http
POST /vaults
```

요청:

```json
{
  "vault_id": "personal-main",
  "name": "Personal Main Vault",
  "description": "개인 지식 저장소",
  "default_visibility": "personal"
}
```

정책:

```text
- vault_id는 필수
- 영문 소문자, 숫자, 하이픈만 허용
- 중복 vault_id 생성 불가
```

---

## 8.4 Vault 목록 조회

```http
GET /vaults
```

응답:

```json
{
  "success": true,
  "data": {
    "vaults": [
      {
        "vault_id": "personal-main",
        "name": "Personal Main Vault",
        "description": "개인 지식 저장소",
        "is_active": true,
        "created_at": "2026-06-24T00:00:00"
      }
    ]
  },
  "error": null
}
```

---

## 8.5 Manifest 동기화

```http
POST /vaults/{vault_id}/sync/manifest
```

요청:

```json
{
  "files": [
    {
      "path": "Company/Autoclip/Issues/electron.md",
      "hash": "abc123",
      "size": 12045,
      "updated_at": "2026-06-24T10:00:00",
      "mime_type": "text/markdown"
    }
  ]
}
```

응답:

```json
{
  "success": true,
  "data": {
    "need_upload": ["Company/Autoclip/Issues/electron.md"],
    "unchanged": [],
    "archived_candidates": [],
    "conflicts": []
  },
  "error": null
}
```

conflict가 있으면 409를 반환한다.

Manifest API는 archive 후보를 반환만 한다.
이 API 호출만으로 `vault_files` 또는 `knowledge_chunks`를 archived 테이블로 이동하지 않는다.

---

## 8.6 파일 업로드

```http
POST /vaults/{vault_id}/sync/files
```

요청:

```json
{
  "path": "Company/Autoclip/Issues/electron.md",
  "hash": "abc123",
  "content": "---\ntitle: ...\n---\n# Electron 문제\n",
  "size": 12045,
  "mime_type": "text/markdown"
}
```

정책:

```text
- path traversal 방지
- 허용 확장자만 저장
- 파일 크기 제한 적용
- 동일 path + 동일 hash면 skip
- 동일 path + 다른 hash면 새 버전으로 저장
- conflict 감지 시 409 반환
```

파일 저장은 아래 순서로 처리한다.

```text
1. 요청 validation
2. content hash 재계산
3. 현재 DB row와 서버 파일 hash 비교
4. 임시 파일 저장
5. atomic rename으로 Vault copy 반영
6. DB row upsert 및 index_status=pending 설정
```

응답:

```json
{
  "success": true,
  "data": {
    "path": "Company/Autoclip/Issues/electron.md",
    "status": "uploaded",
    "hash": "abc123"
  },
  "error": null
}
```

---

## 8.7 Reindex 실행

```http
POST /vaults/{vault_id}/reindex
```

요청:

```json
{
  "mode": "changed_only"
}
```

mode enum:

```text
changed_only
full
file
```

Reindex API는 v1에서 동기 처리한다.
파일 단위 실패는 전체 reindex 실패로 처리하지 않고 `failed_files`에 집계하며, 상세 실패 사유는 `index_failure_logs`에 기록한다.

응답:

```json
{
  "success": true,
  "data": {
    "vault_id": "personal-main",
    "mode": "changed_only",
    "indexed_files": 10,
    "skipped_files": 25,
    "failed_files": 0,
    "created_chunks": 42,
    "updated_chunks": 18,
    "deleted_chunks": 0
  },
  "error": null
}
```

---

## 8.8 파일 단위 Reindex

```http
POST /vaults/{vault_id}/reindex/file
```

요청:

```json
{
  "path": "Company/Autoclip/Issues/electron.md"
}
```

---

## 8.9 Archive 적용

```http
POST /vaults/{vault_id}/sync/archive
```

요청:

```json
{
  "paths": ["Company/Autoclip/Old/removed.md"],
  "reason": "missing_in_manifest"
}
```

정책:

```text
- manifest API가 반환한 archive 후보에 대해서만 호출한다.
- archive 대상 파일의 서버 파일 hash와 DB hash가 다르면 409 Conflict를 반환한다.
- archive 적용 시 vault_files와 knowledge_chunks row를 archive 테이블로 복사한 뒤 원본 테이블에서 제거한다.
```

---

## 8.10 지식 검색

```http
POST /knowledge/search
```

요청:

```json
{
  "vault_id": "personal-main",
  "query": "Electron portable 실행 시 창이 안 뜨는 문제",
  "project": "autoclip",
  "filters": {
    "types": ["rule", "reference", "issue-solution"],
    "status": ["current"],
    "priority": ["high", "medium"],
    "visibility": ["personal", "company"],
    "tags": ["electron"]
  },
  "top_k": 5
}
```

필수값:

```text
vault_id
query
```

`answer_context`는 v1에서 LLM을 호출하지 않는다.
고정 템플릿과 검색 결과 metadata만 사용해 생성한다.

응답:

```json
{
  "success": true,
  "data": {
    "query": "Electron portable 실행 시 창이 안 뜨는 문제",
    "vault_id": "personal-main",
    "project": "autoclip",
    "filters": {
      "status": ["current"],
      "types": ["rule", "reference", "issue-solution"],
      "visibility": ["personal", "company"]
    },
    "answer_context": {
      "summary": "검색어와 metadata를 기반으로 관련 chunk가 반환되었습니다.",
      "recommended_action": "상위 검색 결과의 source_path, heading_path, agent_hint를 우선 확인하세요."
    },
    "results": [
      {
        "rank": 1,
        "score": 0.8421,
        "source_path": "Company/Autoclip/Issues/electron-portable-window.md",
        "title": "Electron Portable 실행 시 창이 뜨지 않는 문제",
        "heading_path": [
          "Electron Portable 실행 시 창이 뜨지 않는 문제",
          "해결 방향"
        ],
        "type": "issue-solution",
        "project": "autoclip",
        "domain": "electron",
        "priority": "high",
        "status": "current",
        "visibility": "personal",
        "tags": ["electron", "windows", "packaging", "pyinstaller"],
        "content": "앱 시작 시 backend.exe 실행 성공 여부를 로그로 남긴다...",
        "agent_hint": "Electron 배포 문제를 수정할 때는 UI 코드만 보지 말고 main process, preload, backend spawn, packaging 설정을 함께 확인한다."
      }
    ]
  },
  "error": null
}
```

---

## 8.11 검색 로그 조회

```http
GET /search-logs
```

query parameters:

```text
vault_id
from
to
query
limit
```

개인용 서비스이므로 관리자 토큰 분리는 하지 않는다.

---

## 9. DB 설계

DB schema는 Alembic migration으로 관리한다.
아래 SQL은 초기 migration의 기준이며, 구현 과정에서 SQLAlchemy 모델과 migration이 같은 구조를 유지해야 한다.
`vaults.id`를 내부 FK 기준으로 사용하고, API에는 계속 `vault_id` 문자열을 노출한다.

## 9.1 vaults

```sql
CREATE TABLE vaults (
    id BIGSERIAL PRIMARY KEY,
    vault_id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    description TEXT,
    default_visibility TEXT NOT NULL DEFAULT 'personal',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT now(),
    updated_at TIMESTAMP NOT NULL DEFAULT now()
);
```

## 9.2 vault_files

```sql
CREATE TABLE vault_files (
    id BIGSERIAL PRIMARY KEY,
    vault_pk BIGINT NOT NULL REFERENCES vaults(id) ON DELETE CASCADE,
    vault_id TEXT NOT NULL,
    source_path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    size_bytes BIGINT,
    mime_type TEXT,
    file_type TEXT,
    vectorize BOOLEAN NOT NULL DEFAULT FALSE,
    status TEXT NOT NULL DEFAULT 'current',
    index_status TEXT NOT NULL DEFAULT 'pending',
    index_error TEXT,
    last_synced_at TIMESTAMP DEFAULT now(),
    last_indexed_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT now(),
    updated_at TIMESTAMP NOT NULL DEFAULT now(),
    UNIQUE (vault_id, source_path)
);
```

## 9.3 knowledge_chunks

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE knowledge_chunks (
    id BIGSERIAL PRIMARY KEY,

    vault_pk BIGINT NOT NULL REFERENCES vaults(id) ON DELETE CASCADE,
    vault_id TEXT NOT NULL,
    source_path TEXT NOT NULL,
    chunk_index INT NOT NULL,

    title TEXT,
    heading TEXT,
    heading_path TEXT[],
    content TEXT NOT NULL,
    agent_hint TEXT,

    project TEXT,
    domain TEXT,
    type TEXT,
    status TEXT NOT NULL DEFAULT 'current',
    priority TEXT NOT NULL DEFAULT 'medium',
    visibility TEXT NOT NULL DEFAULT 'personal',
    tags TEXT[],

    content_hash TEXT NOT NULL,
    embedding_model TEXT NOT NULL DEFAULT 'bge-m3',
    embedding vector(1024),

    created_at TIMESTAMP NOT NULL DEFAULT now(),
    updated_at TIMESTAMP NOT NULL DEFAULT now(),

    UNIQUE (vault_id, source_path, chunk_index)
);
```

## 9.4 archived_vault_files

```sql
CREATE TABLE archived_vault_files (
    id BIGSERIAL PRIMARY KEY,

    original_id BIGINT,
    vault_pk BIGINT,
    vault_id TEXT NOT NULL,
    source_path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    size_bytes BIGINT,
    mime_type TEXT,
    file_type TEXT,

    archived_reason TEXT NOT NULL,
    archived_at TIMESTAMP NOT NULL DEFAULT now(),
    archived_by TEXT DEFAULT 'system',

    raw_record JSONB
);
```

## 9.5 archived_knowledge_chunks

```sql
CREATE TABLE archived_knowledge_chunks (
    id BIGSERIAL PRIMARY KEY,

    original_id BIGINT,
    vault_pk BIGINT,
    vault_id TEXT NOT NULL,
    source_path TEXT NOT NULL,
    chunk_index INT NOT NULL,

    title TEXT,
    heading TEXT,
    heading_path TEXT[],
    content TEXT,
    agent_hint TEXT,

    project TEXT,
    domain TEXT,
    type TEXT,
    status TEXT,
    priority TEXT,
    visibility TEXT,
    tags TEXT[],

    content_hash TEXT,
    embedding_model TEXT,
    embedding vector(1024),

    archived_reason TEXT NOT NULL,
    archived_at TIMESTAMP NOT NULL DEFAULT now(),
    archived_by TEXT DEFAULT 'system',

    raw_record JSONB
);
```

## 9.6 search_logs

```sql
CREATE TABLE search_logs (
    id BIGSERIAL PRIMARY KEY,

    request_id TEXT NOT NULL,
    token_id TEXT,
    vault_pk BIGINT REFERENCES vaults(id) ON DELETE SET NULL,
    vault_id TEXT NOT NULL,

    client_ip TEXT,
    user_agent TEXT,

    query TEXT NOT NULL,
    filters JSONB,
    top_k INT,
    result_count INT,
    latency_ms INT,

    created_at TIMESTAMP NOT NULL DEFAULT now()
);
```

## 9.7 index_failure_logs

```sql
CREATE TABLE index_failure_logs (
    id BIGSERIAL PRIMARY KEY,

    vault_pk BIGINT REFERENCES vaults(id) ON DELETE SET NULL,
    vault_id TEXT NOT NULL,
    source_path TEXT NOT NULL,
    content_hash TEXT,

    phase TEXT NOT NULL,
    error_code TEXT NOT NULL,
    error_message TEXT NOT NULL,
    error_details JSONB,

    created_at TIMESTAMP NOT NULL DEFAULT now()
);
```

`phase` 값:

```text
frontmatter
chunking
embedding
database
unknown
```

## 9.8 추천 인덱스

```sql
CREATE INDEX idx_vault_files_vault_path
ON vault_files (vault_id, source_path);

CREATE INDEX idx_vault_files_index_status
ON vault_files (vault_id, index_status);

CREATE INDEX idx_chunks_vault_project_status
ON knowledge_chunks (vault_id, project, status);

CREATE INDEX idx_chunks_type
ON knowledge_chunks (type);

CREATE INDEX idx_chunks_priority
ON knowledge_chunks (priority);

CREATE INDEX idx_chunks_tags
ON knowledge_chunks USING GIN (tags);

CREATE INDEX idx_search_logs_vault_created
ON search_logs (vault_id, created_at DESC);

CREATE INDEX idx_index_failure_logs_vault_created
ON index_failure_logs (vault_id, created_at DESC);
```

벡터 인덱스는 데이터가 충분히 쌓인 뒤 추가한다.

```sql
CREATE INDEX idx_chunks_embedding_hnsw
ON knowledge_chunks
USING hnsw (embedding vector_cosine_ops);
```

---

## 10. Indexing 상태

`vault_files.index_status`는 아래 값을 가진다.

```text
pending   # 인덱싱 대기
indexed   # 인덱싱 완료
failed    # 인덱싱 실패
skipped   # vectorize=false 또는 임베딩 제외 대상
archived  # archive 처리됨
```

인덱싱 실패 시 `vault_files.index_error`에 최신 실패 원인을 저장한다.
실패 이력은 `index_failure_logs`에 누적 저장한다.

---

## 11. Upload Script 설계

업무 PC에서 실행되는 script는 아래 역할을 가진다.

```text
1. Vault 폴더 스캔
2. 허용 확장자 파일만 수집
3. 제외 확장자 및 제외 폴더 필터링
4. 파일별 SHA-256 hash 계산
5. manifest API 호출
6. need_upload 파일만 업로드
7. archived_candidates가 있으면 archive API 호출
8. reindex API 호출
9. 결과 출력
```

## 11.1 제외 폴더

```text
.obsidian/
.trash/
Archive/    # 선택
Private/    # 선택
```

## 11.2 실행 예시

```bash
python upload_obsidian.py \
  --vault-id personal-main \
  --vault-path "/Users/me/ObsidianVault" \
  --server-url "http://mini-pc:8000" \
  --token "$KNOWLEDGE_API_TOKEN"
```

---

## 12. 보안 정책

## 12.1 Path Traversal 방지

서버는 업로드 path를 저장하기 전에 반드시 안전 경로 검증을 수행한다.

금지 예시:

```text
../../etc/passwd
/absolute/path/file.md
C:\Windows\system32\file
```

## 12.2 파일 제한

- 허용 확장자만 저장한다.
- 파일 크기 제한을 초과하면 실패 처리한다.
- binary 파일은 embedding 대상에서 제외한다.
- `.env`, `.pem`, `.key` 등 민감 파일은 업로드 차단한다.

## 12.3 DB 포트 공개 금지

PostgreSQL 포트는 외부에 공개하지 않는다.
업무 PC와 Agent는 FastAPI만 호출한다.

---

## 13. 운영 정책

## 13.1 Reindex 전략

기본은 변경된 파일만 인덱싱한다.

```text
mode=changed_only
```

전체 재인덱싱은 아래 경우에만 수행한다.

```text
- embedding model 변경
- chunking 규칙 변경
- frontmatter 파싱 규칙 변경
- DB schema 변경으로 재생성 필요
```

## 13.2 모델 변경 정책

v1에서는 모델을 변경하지 않는다.

초기 모델:

```text
provider: Ollama
model: bge-m3
dimension: 1024
```

모델 변경 시:

```text
1. 기존 knowledge_chunks 백업
2. embedding_model 변경
3. full reindex 실행
4. 검색 품질 확인
5. 기존 embedding 제거 또는 archive
```

## 13.3 백업

```text
소스코드:
- Git 저장

Vault copy:
- 주 1회 tar 백업
- 1개월 보관

PostgreSQL:
- pg_dump 백업 권장
- 최소 주 1회
```

---

## 14. MVP 구현 순서

## Phase 1. 기반 구성

```text
1. PostgreSQL pgvector 활성화
2. FastAPI 프로젝트 생성
3. Bearer Token 인증 미들웨어 구현
4. Alembic migration 구성
5. vaults / vault_files / knowledge_chunks / search_logs / index_failure_logs 테이블 생성
6. Python 3.14 고정 및 구현일 기준 최신 호환 의존성 사용
7. Ollama bge-m3 연동 확인
```

## Phase 2. Vault 동기화

```text
1. /vaults 생성 API 구현
2. /sync/manifest 구현
3. /sync/files 구현
4. /sync/archive 구현
5. 업무 PC upload script 구현
6. 파일 저장 및 hash 비교 구현
7. conflict 발생 시 409 반환 구현
```

## Phase 3. 인덱싱

```text
1. frontmatter parser 구현
2. Markdown chunker 구현
3. vectorize=true + status=current 필터링
4. Ollama embedding 생성
5. pgvector 저장
6. index_status 관리
7. 파일 단위 실패를 index_failure_logs에 누적 저장
```

## Phase 4. 검색

```text
1. /knowledge/search 구현
2. vault_id 필수 검증
3. filter 조건 적용
4. pgvector cosine 검색
5. Agent 친화 응답 포맷 구현
6. search_logs 저장
```

## Phase 5. 운영 기능

```text
1. archived 테이블 이동 처리
2. /search-logs 조회 API
3. 백업 스크립트
4. health check 강화
5. FastAPI Dockerfile 구성
```

---

## 15. v1 완료 기준

v1은 아래 조건을 만족하면 완료로 본다.

```text
- 업무 PC에서 upload script로 Vault 파일 업로드 가능
- 변경된 파일만 서버에 업로드됨
- conflict 발생 시 API가 실패 처리함
- 미니PC에서 Markdown 파일이 안전한 경로에 저장됨
- frontmatter 기반으로 vectorize 대상 문서만 인덱싱됨
- bge-m3 embedding이 pgvector에 저장됨
- vault_id 필수 검색 API가 동작함
- 검색 결과가 Agent 친화 포맷으로 반환됨
- 모든 검색 요청이 search_logs에 저장됨
- 삭제된 파일은 archived 테이블로 이동됨
- 파일 단위 reindex 실패가 index_failure_logs에 누적 저장됨
- 주 단위 Vault tar 백업이 가능함
- FastAPI Dockerfile로 서버 이미지를 빌드할 수 있음
```

---

## 16. 추후 개선 후보

```text
- PDF 텍스트 추출 후 임베딩
- 이미지 OCR
- reranker 도입
- 검색 결과 품질 평가셋 구축
- 관리자 UI
- 검색 로그 기반 인기 문서 분석
- Agent별 검색 프리셋
- project별 high priority 문서 자동 로딩
- Obsidian plugin 방식 업로드
```
