# Global Security Review Rerun

Status: PASS
codeQualityStatus: WATCH
recommendation: APPROVE
Confidence: high
Report path: `.omo/evidence/global-review-security-rerun.md`

Scope reviewed:
- Current full uncommitted state in `/Users/ia03060_mac/workspace/qulip/obsidian-sync-go-cli-worktree`, including staged deletions, unstaged modifications, and untracked Go files.
- Previous blockers:
  - symlink-aware vault containment before scanner reads, pull writes/deletes, and conflict writes.
  - tracked executable build artifacts and docs/install paths using `.omo/evidence`.
- Fix evidence inspected:
  - `.omo/evidence/global-fix-symlink-containment.txt`
  - `.omo/evidence/global-fix-build-provenance.txt`

Skill perspective check:
- `omo:remove-ai-slops` was loaded from `/Users/ia03060_mac/.codex/plugins/cache/sisyphuslabs/omo/4.16.0/skills/remove-ai-slops/SKILL.md` and applied as a read-only overfit/slop lens. The new blocker-fix tests are behavior tests, not deletion-only, tautological, or implementation-constant mirrors.
- `omo:programming` was loaded from `/Users/ia03060_mac/.codex/plugins/cache/sisyphuslabs/omo/4.16.0/skills/programming/SKILL.md`; `references/go/README.md` and `references/go/testing.md` were also loaded for the Go/test review lens. No blocker-fix violation of the programming perspective was found.
- `security-review` was loaded from `/Users/ia03060_mac/.codex/skills/ecc/security-review/SKILL.md`.

Command results:
- `git ls-files .omo/evidence/builds .omo/evidence/obsidian-sync-agent` -> exit 0, no output.
- `go test -count=1 ./...` -> exit 0.
- `go vet ./...` -> exit 0.
- `git diff --check` -> exit 0.
- `git diff --cached --check` -> exit 0.
- `find .omo/evidence -type f -perm -111 -print` -> exit 0, no output.
- `go test -count=1 -v ./internal/syncagent/scanner ./internal/syncagent/conflict ./internal/syncagent/engine -run 'TestScanVault_skipsSymlinkedMarkdownFile_whenTargetIsOutsideVault|TestWriteConflictFile_rejectsSymlinkedParent_whenTargetIsOutsideVault|TestRunSyncPullWriteRejectsSymlinkedParent_whenTargetIsOutsideVault|TestRunSyncPullDeleteRejectsSymlinkedParent_whenTargetIsOutsideVault|TestRunSyncPullDeleteRejectsSymlinkedFile_whenTargetIsOutsideVault'` -> exit 0.
- High-confidence secret scan over `git diff HEAD -- .` for OpenAI/AWS/Slack/GitHub/private-key patterns -> exit 1, no matches; exit 1 is ripgrep's no-match code.
- Broader keyword scan matched only placeholders, environment variable names, and test literals such as `your-api-key`, `osk_...`, and `secret`; no real unredacted secret was identified.

## CRITICAL

None.

## HIGH

None.

## MEDIUM

1. Carried forward: Obsidian TLS verification can still be disabled for non-loopback HTTPS endpoints.

Evidence:
- `internal/syncagent/obsidian/obsidian.go:98` through `internal/syncagent/obsidian/obsidian.go:102` configures `InsecureSkipVerify` whenever `verify_tls` is false.
- `internal/syncagent/obsidian/obsidian.go:178` through `internal/syncagent/obsidian/obsidian.go:180` sends the Obsidian API key as a bearer token.

Assessment:
- This is unchanged from the prior global security review and is not introduced by the blocker fixes. It remains non-blocking under this rerun's requested criteria because no new HIGH/CRITICAL blocker was introduced.

2. Carried forward: Obsidian Local REST API key is accepted from config file.

Evidence:
- `internal/syncagent/config/doc.go:78` through `internal/syncagent/config/doc.go:83` includes `api_key` in file config.
- `internal/syncagent/config/doc.go:121` keeps the main sync API token env-only, but Obsidian API key handling remains file-or-env via the Obsidian config path.

Assessment:
- This is unchanged from the prior global security review and is not introduced by the blocker fixes.

## LOW

1. Release hygiene: the reviewed security fix currently depends on untracked files.

Evidence:
- `git status --short internal/syncagent/vaultfs internal/syncagent/engine/symlink_containment_test.go internal/syncagent/engine/push_conflict_test.go` reports:
  - `?? internal/syncagent/vaultfs/`
  - `?? internal/syncagent/engine/symlink_containment_test.go`
  - `?? internal/syncagent/engine/push_conflict_test.go`

Assessment:
- The user requested review of the full uncommitted state, so these files were included in this review and in the passing test run. Before final commit/PR, they must be added; otherwise the tracked diff alone will not contain the symlink containment implementation/tests. This is not a current-state security blocker.

2. Residual local filesystem race window after `SafePath`.

Evidence:
- `internal/syncagent/vaultfs/vaultfs.go:54` through `internal/syncagent/vaultfs/vaultfs.go:69` rejects symlink components by `Lstat` before returning a safe path.
- Subsequent reads/writes/deletes use ordinary path operations, for example `internal/syncagent/engine/pull.go:89`, `internal/syncagent/engine/pull.go:94`, `internal/syncagent/engine/pull.go:102`, and `internal/syncagent/engine/pull.go:146`.

Assessment:
- The prior practical blocker is fixed for symlinks present before operation. A concurrent local actor with write access to the vault could still race path components between validation and use. That is a broader TOCTOU hardening topic and not a remaining HIGH blocker for the requested rerun.

## Verification

Symlink scanner containment:
- `internal/syncagent/scanner/scanner.go:34` walks the vault.
- `internal/syncagent/scanner/scanner.go:38` skips symlink entries before `entry.Info()` at line 55 and before `hashFile(path)` at line 59.
- Targeted test `TestScanVault_skipsSymlinkedMarkdownFile_whenTargetIsOutsideVault` passed. This prevents direct upload of symlinked outside-vault Markdown content. Symlinked directories are also skipped by the same symlink-entry check; `WalkDir` does not descend through them.

Pull write/delete containment:
- `internal/syncagent/engine/pull.go:61` calls `vaultfs.SafePath` before constructing the `pullChange`.
- `internal/syncagent/vaultfs/vaultfs.go:28` through `internal/syncagent/vaultfs/vaultfs.go:40` returns a path only after lexical validation and symlink-component rejection.
- `internal/syncagent/vaultfs/vaultfs.go:61` through `internal/syncagent/vaultfs/vaultfs.go:69` uses `os.Lstat` and rejects `os.ModeSymlink`.
- The validated `safeDestination` is used for delete hashing/removal, local reads, and pull writes at `internal/syncagent/engine/pull.go:89`, `internal/syncagent/engine/pull.go:94`, `internal/syncagent/engine/pull.go:102`, `internal/syncagent/engine/pull.go:146`, and `internal/syncagent/engine/pull.go:160`.
- Targeted pull symlink parent/target tests passed and verified outside files were not written or deleted.

Conflict write containment:
- `internal/syncagent/conflict/conflict.go:84` calls `vaultfs.SafePath` for generated conflict destinations before `ReadDir`, `MkdirAll`, or atomic write.
- `internal/syncagent/conflict/conflict.go:108` skips symlinked existing conflict candidates before parsing.
- Targeted conflict symlink-parent test passed and verified no outside conflict file was written.

Build provenance and evidence artifacts:
- `Makefile:4` now uses `dist/obsidian-sync-agent`.
- `README.md:349` through `README.md:355` and `docs/sync-agent.md:17` through `docs/sync-agent.md:28` install from and describe ignored `dist/obsidian-sync-agent` outputs.
- `.gitignore:19` through `.gitignore:20` ignores `dist/`.
- `git ls-files .omo/evidence/builds .omo/evidence/obsidian-sync-agent` produced no output.
- `find .omo/evidence -type f -perm -111 -print` produced no output.
- Ignored local build artifacts exist under `dist/obsidian-sync-agent/`, which is acceptable for the requested criteria.

Blockers:
- None.

Final decision:
- PASS. The two previous HIGH blockers are addressed in the current full uncommitted state, and no new HIGH/CRITICAL security blocker was found.
