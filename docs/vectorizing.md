# Vectorizing

Sync API에 파일이 저장되면 자동으로 vector 인덱싱 대기 상태로 표시됩니다. 이후 reindex 호출로 Ollama bge-m3 임베딩을 생성하고 PostgreSQL pgvector에 저장합니다.

---

## Sync 커밋 후 흐름

```
PUT /vaults/{vault_id}/files/{path} (성공)
        |
        | vault_files.vectorize = true (if .md and not excluded)
        | vault_files.index_status = 'pending'
        v
POST /vaults/{vault_id}/reindex  {"mode": "changed_only"}
        |
        | list_reindex_files: index_status != 'indexed'
        v
  각 파일에 대해:
    1. parse_frontmatter()  →  vectorize=true, status='current' 확인
    2. chunk_markdown()     →  heading 기반 청크 분할
    3. Ollama embed()       →  bge-m3, 1024차원 벡터
    4. replace_chunks()     →  knowledge_chunks 행 교체 (기존 청크 삭제 후 삽입)
    5. index_status = 'indexed'
```

PUT 커밋 후 reindex는 자동으로 실행되지 않습니다. 클라이언트(에이전트 또는 외부 스크립트)가 `POST /vaults/{vault_id}/reindex`를 명시적으로 호출해야 합니다.

RESTORE 이벤트도 `index_status='pending'`으로 설정하므로, 복구된 파일도 다음 reindex에서 자동으로 처리됩니다.

---

## 제외 패턴

다음 파일은 벡터 인덱싱 대상에서 제외됩니다 (`is_vectorizable_path()` 반환값 False).

| 패턴 | 제외 이유 |
|---|---|
| `.md`가 아닌 파일 | Markdown만 지원 |
| `*.conflict.*.md` | 에이전트 conflict 파일 |
| `*.sync-conflict*.md` | 외부 sync 도구 conflict 파일 |
| `.obsidian/**` | Obsidian 앱 설정 |
| `.obsidian-sync-agent/**` | 에이전트 내부 데이터 |
| `.trash/**` | Obsidian 휴지통 |
| `deleted=true` 파일 | Soft-deleted 파일 |
| frontmatter `vectorize: false` | 파일 단위 비활성화 |
| frontmatter `status` ≠ `current` | `draft`, `deprecated`, `archived` 제외 |

위 패턴에 해당하는 파일은 `index_status='skipped'`으로 설정되고 기존 청크가 삭제됩니다.

---

## Reindex 호출

### changed_only (일반 사용)

`index_status != 'indexed'`인 파일만 처리합니다. PUT/RESTORE 후 pending 상태가 된 파일을 인덱싱하는 일반적인 방법입니다.

```bash
curl -sS -X POST "http://localhost:8000/vaults/personal-main/reindex" \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"mode":"changed_only"}'
```

### full (전체 재구축)

모든 `.md` 파일을 다시 임베딩합니다. `index_status`에 관계없이 처리합니다.

```bash
curl -sS -X POST "http://localhost:8000/vaults/personal-main/reindex" \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"mode":"full"}'
```

**응답 예시:**

```json
{
  "success": true,
  "data": {
    "vault_id": "personal-main",
    "mode": "changed_only",
    "indexed_files": 3,
    "skipped_files": 1,
    "failed_files": 0,
    "created_chunks": 12,
    "updated_chunks": 0,
    "deleted_chunks": 2,
    "failures": []
  },
  "error": null
}
```

### 단일 파일 reindex

```bash
curl -sS -X POST "http://localhost:8000/vaults/personal-main/reindex/file" \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"path":"Notes/example.md"}'
```

---

## Soft Delete / Restore 시 벡터 인덱스 동작

### Soft Delete

`DELETE /vaults/{vault_id}/files/{path}` 성공 시:

1. `knowledge_chunks`에서 해당 파일의 청크를 즉시 삭제 (`SyncRepository.delete_chunks`)
2. `vault_files.index_status = 'skipped'`로 설정
3. canonical 파일은 보존 기간 만료 전까지 디스크에 유지 (벡터 인덱스에는 없음)

### Restore

`POST /vaults/{vault_id}/sync/restore` 성공 시:

1. `vault_files.index_status = 'pending'`으로 설정
2. `vault_files.vectorize = true`로 설정 (파일이 vectorizable한 경우)
3. 다음 `POST /reindex?mode=changed_only` 실행 시 자동으로 재임베딩

---

## Conflict 파일은 인덱싱되지 않음

`*.conflict.*.md` 및 `*.sync-conflict*.md` 패턴에 해당하는 파일은 `is_vectorizable_path()`에서 False를 반환하므로 절대 인덱싱되지 않습니다. sync-agent는 이 파일을 업로드하지 않으며, sync API에 직접 PUT해도 제외 경로로 검증되어 400 응답이 반환됩니다.

---

## Vector DB 재구축 가능성

`knowledge_chunks` 테이블은 **파생 데이터**입니다. canonical vault 파일(`vaults/<vault_id>/...`)과 `vault_file_versions` 테이블에 원본 내용이 보존되어 있으므로, `knowledge_chunks`를 전부 삭제하더라도 `mode=full`로 완전히 재구축할 수 있습니다.

```bash
# 필요 시 전체 재구축
curl -sS -X POST "http://localhost:8000/vaults/personal-main/reindex" \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"mode":"full"}'
```

재구축 시 Ollama bge-m3 모델이 실행 중이어야 합니다.

---

## 인덱싱 실패 처리

인덱싱에 실패한 파일은:

1. `vault_files.index_status = 'failed'`, `index_error`에 오류 메시지 저장
2. `index_failure_logs` 테이블에 단계(`frontmatter`, `embedding`, `unknown`)와 함께 기록
3. `changed_only` reindex에서 다시 처리 대상에 포함 (`index_status != 'indexed'`이므로)

실패 원인:
- frontmatter 누락 또는 형식 오류
- Ollama 연결 불가 또는 임베딩 차원 불일치 (설정값 1024)
- vault 파일 읽기 실패

---

## Markdown frontmatter 요구 사항

vectorize 대상 파일은 다음 frontmatter가 필요합니다.

```yaml
---
title: 노트 제목
type: reference
project: obsidian-sync
domain: backend
status: current
priority: medium
visibility: personal
tags: [fastapi, python]
vectorize: true
created: 2026-07-07
updated: 2026-07-07
---
```

| 필드 | 허용 값 |
|---|---|
| `type` | `reference`, `rule`, `decision`, `issue-solution`, `study-note`, `prompt`, `command`, `checklist` |
| `status` | `current`, `draft`, `deprecated`, `archived` |
| `priority` | `high`, `medium`, `low` |
| `visibility` | `personal`, `company`, `confidential`, `public` |
| `vectorize` | `true`, `false` |

`vectorize: false` 또는 `status: current`가 아닌 파일은 인덱싱에서 제외되고 기존 청크가 삭제됩니다.
