---
name: knowledge-management
description: 에이전트가 학습한 내용, 문제 해결 기록, 코딩 패턴 등을 Obsidian vault에 마크다운 노트로 저장한다. obsidian-sync API를 통해 vault 목록을 조회하고, 주제에 맞는 vault를 선택해 노트를 생성한다.
---

# knowledge-management

현재 세션에서 학습하거나 해결한 내용을 Obsidian vault에 저장한다.

## Step 1 — vault 목록 조회 및 선택

```bash
python ~/.claude/skills/knowledge-management/scripts/list_vaults.py \
  --server-url "$OBSIDIAN_SYNC_URL" \
  --token "$KNOWLEDGE_API_TOKEN"
```

출력된 vault 목록의 `name`과 `description`을 보고, 현재 주제·프로젝트에 가장 적합한 vault를 선택한다.

## Step 2 — 노트 작성 및 저장

여러 줄 내용은 임시 파일을 통해 전달한다:

```bash
cat > /tmp/knowledge_note.md << 'EOF'
<마크다운 내용>
EOF

python ~/.claude/skills/knowledge-management/scripts/save_knowledge.py \
  --server-url "$OBSIDIAN_SYNC_URL" \
  --token "$KNOWLEDGE_API_TOKEN" \
  --vault-id "<선택한 vault_id>" \
  --title "<제목>" \
  --content-file /tmp/knowledge_note.md \
  --tags "<태그1,태그2>" \
  --project "<현재 프로젝트명>"

rm /tmp/knowledge_note.md
```

## 노트 작성 원칙

- **배경**: 어떤 문제 또는 상황이었는지
- **핵심 내용**: 배운 것, 해결한 것, 발견한 패턴
- **예시 코드**: 관련 코드가 있다면 코드 블록으로
- **참고**: 관련 파일, 문서, 링크

몇 달 뒤에 읽어도 맥락이 복원될 수 있도록 자기 완결적으로 작성한다.

태그는 언어(`python`, `typescript`), 프레임워크(`fastapi`, `react`), 주제(`auth`, `testing`, `architecture`) 조합으로 사용한다.
