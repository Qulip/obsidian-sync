-- 일회성 정리: 이미 해소된 sync_conflicts 행을 RESOLVED로 전환한다.
--
-- 배경: 쓰기 성공 시 열린 충돌을 해소하는 로직이 들어오기 전(커밋
-- efca202)에는 sync_conflicts 행이 OPEN으로만 기록되고 RESOLVED로
-- 바뀌는 경로가 없었다. 그래서 sync/status의 open_conflicts는 단조
-- 증가만 했다. 새 코드는 앞으로 발생하는 충돌만 정리하므로, 그 이전에
-- 쌓인 행은 이 스크립트로 한 번 정리해야 한다.
--
-- 판정 기준(새 코드의 의미를 소급 적용): 충돌 이후 해당 경로에 새
-- 정본이 내려앉았거나 경로 자체가 사라졌다면 divergence는 이미
-- 해소된 것이다.
--   1. vault_files에 해당 경로 행이 없음      -> 해소
--   2. 해당 행이 삭제됨(deleted = true)        -> 해소
--   3. 현재 revision > 충돌 시점 server_revision -> 해소
-- 위 어디에도 해당하지 않는 행(파일이 살아 있고 충돌 이후 새 리비전이
-- 없는 경우)은 진짜로 아직 미해소이므로 OPEN으로 남긴다.
--
-- 사용법:
--   psql "$OBSIDIAN_SYNC_DATABASE_URL" -f scripts/resolve_stale_sync_conflicts.sql
--
-- 기본 동작은 DRY RUN이다. 실제로 반영하려면 아래 :apply 변수를 켠다:
--   psql "$OBSIDIAN_SYNC_DATABASE_URL" -v apply=1 -f scripts/resolve_stale_sync_conflicts.sql

\set ON_ERROR_STOP on
\if :{?apply}
\else
  \set apply 0
\endif

BEGIN;

-- 정리 대상을 한 번만 계산해 미리보기와 UPDATE가 같은 집합을 보게 한다.
CREATE TEMP TABLE stale_sync_conflicts ON COMMIT DROP AS
SELECT
    c.id,
    c.vault_id,
    c.source_path,
    c.server_revision,
    c.client_base_revision,
    c.device_id,
    c.created_at,
    CASE
        WHEN f.id IS NULL THEN 'file row missing'
        WHEN f.deleted THEN 'file deleted'
        ELSE 'newer revision landed'
    END AS reason
FROM obsidian.sync_conflicts AS c
LEFT JOIN obsidian.vault_files AS f
    ON f.vault_id = c.vault_id
   AND f.source_path = c.source_path
WHERE c.status = 'OPEN'
  AND (
      f.id IS NULL
      OR f.deleted
      OR f.revision > c.server_revision
  );

\echo '--- 정리 대상 (사유별 건수) ---'
SELECT vault_id, reason, count(*) AS rows
FROM stale_sync_conflicts
GROUP BY vault_id, reason
ORDER BY vault_id, reason;

\echo '--- 정리 대상 상세 (최대 50건) ---'
SELECT id, vault_id, source_path, server_revision, reason, created_at
FROM stale_sync_conflicts
ORDER BY vault_id, source_path, id
LIMIT 50;

\echo '--- 정리 후에도 OPEN으로 남는 행 (실제 미해소) ---'
SELECT c.vault_id, count(*) AS rows
FROM obsidian.sync_conflicts AS c
WHERE c.status = 'OPEN'
  AND c.id NOT IN (SELECT id FROM stale_sync_conflicts)
GROUP BY c.vault_id
ORDER BY c.vault_id;

\if :apply
    UPDATE obsidian.sync_conflicts AS c
    SET status = 'RESOLVED',
        resolved_at = now()
    FROM stale_sync_conflicts AS s
    WHERE c.id = s.id
      AND c.status = 'OPEN';

    \echo '--- 반영 완료. vault별 남은 open_conflicts ---'
    SELECT vault_id, count(*) AS open_conflicts
    FROM obsidian.sync_conflicts
    WHERE status = 'OPEN'
    GROUP BY vault_id
    ORDER BY vault_id;

    COMMIT;
\else
    \echo '--- DRY RUN: 아무것도 변경하지 않았다. 반영하려면 -v apply=1 을 붙인다. ---'
    ROLLBACK;
\endif
