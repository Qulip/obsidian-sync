# Gate Review: go-sync-cli-migration Task 7 Reverify

recommendation: REJECT

originalIntent:
- Re-verify Task 7 after prior gate blockers were reportedly fixed.
- Task 7 intent is to port sync engine orchestration and status behavior under `internal/syncagent/engine/**` while preserving the documented/Python lifecycle: validate/load manifest, register device, first pull, same-run pull-conflict push skip, scan, push create/update/delete with correct base revisions, resolved conflict handling, second pull with `device_id`, manifest save, Obsidian refresh summary, status report, dry-run no writes/pushes, and no auto-merge conflicts.

desiredOutcome:
- Return `confirmed` only if the previous blockers are fully resolved and direct verification supports the lifecycle, evidence, test quality, and adversarial classes.

userOutcomeReview:
- Functional lifecycle coverage is mostly supported by source inspection and direct test reruns.
- `RunSync` loads/validates manifest, registers the device, pulls first without `device_id`, scans, pushes, pulls again with `cfg.DeviceID`, saves the manifest, and records Obsidian summary fields.
- Tests cover pull write/delete/cursor, create/update/delete pushes and base revisions, same-run pull-conflict push skip, no auto-merge pull conflict, push conflict file creation, resolved conflict push using conflict revision, server-delete acceptance, dry-run no writes/pushes/manifest save, status report, and Obsidian warning summary.
- The prior review-work evidence blocker is substantially addressed: `.omo/evidence/task-7-review-work.txt` now explicitly contains programming, remove-ai-slops, overfit/test-quality, manual QA, and adversarial-class sections.
- The prior parameter-list blocker is not fully resolved under the loaded `programming` and `remove-ai-slops` criteria. Pull/push production helpers were improved, but direct inspection still found helper arity slop and unsupported/narrow evidence.

blockers:
1. Remaining helper parameter-list slop in current Task 7 code.
   - `internal/syncagent/engine/engine.go:106` defines `plan(ctx, cfg, state, syncClient, summary)` with five parameters.
   - Loaded `programming` criteria treat more than three parameters as parameter bloat requiring grouping or a specific justification.
   - The user's reverify criterion says to confirm no excessive helper parameter lists remain; this is not satisfied.

2. Test helper arity slop remains in the changed Task 7 test surface.
   - `internal/syncagent/engine/test_helpers_test.go:34` `change(...)` has five parameters.
   - `internal/syncagent/engine/test_helpers_test.go:67` `writeNote(...)` has four parameters.
   - `internal/syncagent/engine/test_helpers_test.go:87` `requireFileContent(...)` has four parameters.
   - `internal/syncagent/engine/test_helpers_test.go:158` `requirePut(...)` has five parameters.
   - `internal/syncagent/engine/test_helpers_test.go:168` `requireDelete(...)` has four parameters.
   - These are not enough alone to prove user-visible behavior is wrong, but they are unresolved slop in the changed test code and contradict the broad no-helper-arity claim.

3. Post-fix evidence is too narrow for the stated blocker.
   - `.omo/evidence/task-7-fix-after.txt` only says: "No production pull/push helper signatures with 4+ comma-separated parameters after refactor."
   - `.omo/evidence/task-7-review-work.txt:16` limits the excessive-param fix to rejected `pull.go`/`push.go` signatures.
   - `.omo/evidence/task-7-review-work.txt:38` says the scan is only for production pull/push helpers.
   - This does not support the requested broader verification of "no excessive helper parameter lists remain."

checkedArtifactPaths:
- `/Users/ia03060_mac/workspace/qulip/obsidian-sync-go-cli-worktree/internal/syncagent/engine/engine.go`
- `/Users/ia03060_mac/workspace/qulip/obsidian-sync-go-cli-worktree/internal/syncagent/engine/pull.go`
- `/Users/ia03060_mac/workspace/qulip/obsidian-sync-go-cli-worktree/internal/syncagent/engine/push.go`
- `/Users/ia03060_mac/workspace/qulip/obsidian-sync-go-cli-worktree/internal/syncagent/engine/types.go`
- `/Users/ia03060_mac/workspace/qulip/obsidian-sync-go-cli-worktree/internal/syncagent/engine/util.go`
- `/Users/ia03060_mac/workspace/qulip/obsidian-sync-go-cli-worktree/internal/syncagent/engine/*_test.go`
- `/Users/ia03060_mac/workspace/qulip/obsidian-sync-go-cli-worktree/.omo/evidence/task-7-review-work.txt`
- `/Users/ia03060_mac/workspace/qulip/obsidian-sync-go-cli-worktree/.omo/evidence/task-7-fix-before.txt`
- `/Users/ia03060_mac/workspace/qulip/obsidian-sync-go-cli-worktree/.omo/evidence/task-7-fix-after.txt`
- `/Users/ia03060_mac/workspace/qulip/obsidian-sync-go-cli-worktree/.omo/evidence/task-7-go-test-engine.txt`
- `/Users/ia03060_mac/workspace/qulip/obsidian-sync-go-cli-worktree/.omo/evidence/task-7-go-vet-engine.txt`
- `/Users/ia03060_mac/workspace/qulip/obsidian-sync-go-cli-worktree/.omo/evidence/task-7-go-test-all.txt`
- `/Users/ia03060_mac/workspace/qulip/obsidian-sync-go-cli-worktree/.omo/evidence/task-7-go-test-race-engine.txt`
- `/Users/ia03060_mac/workspace/qulip/obsidian-sync-go-cli-worktree/.omo/evidence/task-7-qa-create-update-delete.txt`
- `/Users/ia03060_mac/workspace/qulip/obsidian-sync-go-cli-worktree/.omo/evidence/task-7-qa-conflict-no-auto-merge.txt`
- `/Users/ia03060_mac/workspace/qulip/obsidian-sync-go-cli-worktree/.omo/evidence/task-7-qa-obsidian-summary.txt`
- `/Users/ia03060_mac/workspace/qulip/obsidian-sync-go-cli-worktree/.omo/evidence/task-7-debug-audit.txt`
- `/Users/ia03060_mac/workspace/qulip/obsidian-sync-go-cli-worktree/.omo/plans/go-sync-cli-migration.md`
- `/Users/ia03060_mac/workspace/qulip/obsidian-sync-go-cli-worktree/docs/sync-agent.md`
- `/Users/ia03060_mac/workspace/qulip/obsidian-sync-go-cli-worktree/src/obsidian_sync/sync_agent/engine.py`

verificationCommands:
- `go test -count=1 ./internal/syncagent/engine` PASS
- `go vet ./internal/syncagent/engine` PASS
- `go test -count=1 ./...` PASS
- `go test -race -count=1 ./internal/syncagent/engine` PASS
- `go test -count=1 ./internal/syncagent/engine -run TestRunSyncCreateUpdateDelete -v` PASS
- `go test -count=1 ./internal/syncagent/engine -run TestRunSyncConflictDoesNotAutoMerge -v` PASS
- `go test -count=1 ./internal/syncagent/engine -run TestRunSyncReportsObsidianRefreshWarning -v` PASS
- `go test -shuffle=on -count=1 ./internal/syncagent/engine` PASS
- `gofmt -l internal/syncagent/engine/*.go` produced no output

adversarialProbes:
- dirty_worktree: Current worktree is `/Users/ia03060_mac/workspace/qulip/obsidian-sync-go-cli-worktree` on branch `work/go-sync-cli-migration`; dirty set is Task 7 engine/evidence plus tracked `internal/syncagent/engine/doc.go`.
- stale_state: Verified `pwd`, branch, HEAD `dc6d5a3c3c14fc8f749766ff1f7d3d3ecd244a7b`, direct source contents, and reran commands after artifact inspection.
- misleading_success_output: Did not rely on executor artifacts; reran all requested commands directly and inspected source/evidence line references.
- malformed_input: Source has guards for manifest vault mismatch, unsafe server/local paths, and server hash mismatch. Evidence claims "existing engine tests" cover malformed cases, but direct search found mostly source guards rather than dedicated engine tests for unsafe path/hash mismatch.
- flaky_tests/hung_commands: Required commands and shuffle probe completed within tool timeouts; no sleeps or wall-clock waits found in engine tests.

exactEvidenceGaps:
- `.omo/evidence/task-7-fix-after.txt` is a one-line narrow scan for production pull/push helpers only; it misses `engine.go:106` and changed test helpers.
- `.omo/evidence/task-7-review-work.txt` claims slop criteria were checked, but the current artifact does not explain or justify remaining >3 parameter helpers.
- The malformed-input adversarial claim in `.omo/evidence/task-7-review-work.txt:33` overstates test coverage; direct inspection found code guards but not dedicated engine tests for all named malformed cases.

residualNotes:
- Pull/push production helper signatures from the previous blocker were refactored successfully.
- The Task 7 functional tests and required Go commands are green, but green tests do not resolve the remaining slop/evidence blockers.
