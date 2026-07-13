---
name: knowledge-management
description: 에이전트가 학습한 내용, 문제 해결 기록, 코딩 패턴 등을 obsidian-sync MCP tool로 Obsidian vault에 마크다운 노트로 저장한다. MCP tool을 사용할 수 없을 때만 번들된 fallback 스크립트를 사용한다.
---

# knowledge-management

현재 세션에서 학습하거나 해결한 내용을 Obsidian vault에 저장한다.

## Prerequisites

- obsidian-sync MCP endpoint를 Agent에 등록한다: `$OBSIDIAN_SYNC_URL/mcp`
- MCP client가 `Authorization: Bearer $KNOWLEDGE_API_TOKEN` 헤더를 보내게 설정한다.
- `KNOWLEDGE_API_TOKEN`은 DB API token이어야 한다. Admin token을 사용하지 않는다.
- 토큰을 로그, 답변, 노트 본문에 쓰지 않는다.

## Step 1 - vault 목록 조회 및 선택

MCP tool이 있으면 먼저 `list_vaults_mcp_vaults_get`을 호출한다.
출력된 `vault_id`, `name`, `description`을 보고 현재 주제와 프로젝트에 가장
적합한 vault를 선택한다.

MCP tool을 사용할 수 없을 때만 fallback 스크립트를 사용한다:

```bash
python <skill-dir>/scripts/list_vaults.py \
  --server-url "$OBSIDIAN_SYNC_URL" \
  --token "$KNOWLEDGE_API_TOKEN"
```

## Step 2 - 노트 작성 및 저장

노트는 자기 완결적인 Markdown으로 작성한다. 경로는 기본적으로
`Agent Notes/YYYY-MM-DD-<slug>.md` 형식을 사용한다.

MCP tool이 있으면 `sync_file_mcp_vaults__vault_id__sync_file_post`를 호출한다:

```json
{
  "vault_id": "<선택한 vault_id>",
  "path": "Agent Notes/YYYY-MM-DD-short-title.md",
  "content": "---\ntitle: \"<제목>\"\ntype: study-note\nproject: <프로젝트명>\ndomain: knowledge-management\nstatus: current\npriority: medium\nvisibility: personal\ntags: [python, fastapi]\nvectorize: true\ncreated: YYYY-MM-DD\nupdated: YYYY-MM-DD\nsource: agent\n---\n\n# <제목>\n\n<마크다운 내용>\n",
  "mime_type": "text/markdown"
}
```

저장 후 검색 가능하게 만들 필요가 있으면
`reindex_vault_mcp_vaults__vault_id__reindex_post`를 `{"mode": "changed_only"}`
로 호출한다. Ollama나 embedding 서비스가 준비되지 않은 경우 reindex 실패는 저장
실패로 취급하지 않는다.

이 저장 호출은 fail-closed다: 같은 경로에 내용이 다른 노트가 이미 있으면
409 CONFLICT_DETECTED를 반환하고 덮어쓰지 않는다. 의도적으로 교체하려면
`overwrite: true`를 함께 보낸다. 내용이 동일하면 `overwrite` 값과 무관하게
`status: "skipped"`로 no-op 처리된다.

MCP tool을 사용할 수 없을 때만 fallback 스크립트를 사용한다:

```bash
python <skill-dir>/scripts/save_knowledge.py \
  --server-url "$OBSIDIAN_SYNC_URL" \
  --token "$KNOWLEDGE_API_TOKEN" \
  --vault-id "<선택한 vault_id>" \
  --title "<제목>" \
  --content-file /path/to/note.md \
  --tags "python,fastapi" \
  --project "<현재 프로젝트명>"
```

같은 경로에 내용이 다른 노트가 이미 있으면 스크립트는 409 오류 메시지를 출력하고
exit code 1로 종료한다. 의도적으로 교체하려면 `--overwrite` 플래그를 추가한다.

## 노트 작성 원칙

- **배경**: 어떤 문제 또는 상황이었는지
- **핵심 내용**: 배운 것, 해결한 것, 발견한 패턴
- **예시 코드**: 관련 코드가 있다면 코드 블록으로
- **참고**: 관련 파일, 문서, 링크

몇 달 뒤에 읽어도 맥락이 복원될 수 있도록 자기 완결적으로 작성한다.

태그는 언어(`python`, `typescript`), 프레임워크(`fastapi`, `react`), 주제(`auth`, `testing`, `architecture`) 조합으로 사용한다.
