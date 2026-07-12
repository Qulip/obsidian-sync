# Gate Review: go-sync-cli-migration Task 7

recommendation: REJECT

## originalIntent

Port Task 7 of `go-sync-cli-migration`: implement Go sync engine orchestration and status behavior under `internal/syncagent/engine/**`, matching the Python lifecycle and `docs/sync-agent.md` contract without editing Python/server packages or adjacent Go packages.

## desiredOutcome

Users should get a behavior-compatible engine that validates and loads the manifest, registers the device, pulls first, avoids same-run push for pull-conflicted paths, scans the vault, pushes create/update/delete with correct base revisions, handles unresolved/resolved conflicts without auto-merge, accepts resolved server deletes, pulls again with `device_id`, saves the manifest, reports Obsidian refresh summary, supports dry-run without writes/pushes, and returns status data.

## userOutcomeReview

The implementation and tests largely support the functional lifecycle. Direct source inspection confirms the main order in `RunSync`: load/validate manifest, create client, dry-run plan or normal run, save manifest, then Obsidian refresh. Normal `run` registers the device, pulls with an empty `device_id`, records pull conflict paths to skip, scans the vault, pushes local changes, then pulls again with `cfg.DeviceID`. Requested commands pass, including stronger uncached full-suite and race checks.

However, the gate cannot approve because the required code-review evidence does not explicitly cover the `remove-ai-slops`/`programming` skill perspectives or overfit/slop criteria, and the direct slop pass found unresolved new-code parameter bloat that creates maintenance burden.

## blockers

1. Missing required review/slop evidence coverage.
   - `.omo/evidence/task-7-review-work.txt` has generic review lanes and a size check, but it does not explicitly show `remove-ai-slops` criteria, overfit/tautological-test review, deletion-only/implementation-mirroring-test review, or `programming` criteria coverage.
   - Gate instruction requires rejecting when that report coverage is absent, missing, or unsupported.

2. Direct slop/programming pass found unresolved excessive parameter lists in new production code.
   - `internal/syncagent/engine/pull.go:18` `pull(...)` has 7 parameters.
   - `internal/syncagent/engine/pull.go:57` `applyChange(...)` has 8 parameters.
   - `internal/syncagent/engine/pull.go:84` `applyDelete(...)` has 8 parameters.
   - `internal/syncagent/engine/pull.go:137` `applyWrite(...)` has 9 parameters.
   - `internal/syncagent/engine/pull.go:179` `writePullConflict(...)` has 10 parameters.
   - `internal/syncagent/engine/push.go:17` `push(...)` has 9 parameters.
   - `internal/syncagent/engine/push.go:80` `pushUpsert(...)` has 8 parameters.
   - `internal/syncagent/engine/push.go:123` `pushDelete(...)` has 8 parameters.
   - `internal/syncagent/engine/push.go:160` `pushConflict(...)` has 9 parameters.
   - This violates the loaded `remove-ai-slops` excessive-complexity criterion for long parameter lists over 5 args without a struct/object and the loaded `programming` parameter-bloat criterion. Related state should be grouped rather than threaded through every helper.

## checkedArtifactPaths

- `.omo/plans/go-sync-cli-migration.md`
- `.omo/drafts/go-sync-cli-migration.md`
- `docs/sync-agent.md`
- `src/obsidian_sync/sync_agent/engine.py`
- `internal/syncagent/engine/doc.go`
- `internal/syncagent/engine/engine.go`
- `internal/syncagent/engine/pull.go`
- `internal/syncagent/engine/push.go`
- `internal/syncagent/engine/types.go`
- `internal/syncagent/engine/util.go`
- `internal/syncagent/engine/engine_test.go`
- `internal/syncagent/engine/dry_run_test.go`
- `internal/syncagent/engine/status_test.go`
- `internal/syncagent/engine/fake_client_test.go`
- `internal/syncagent/engine/test_helpers_test.go`
- `internal/syncagent/manifest/manifest.go`
- `internal/syncagent/scanner/scanner.go`
- `internal/syncagent/conflict/conflict.go`
- `internal/syncagent/client/dto.go`
- `internal/syncagent/client/errors.go`
- `internal/syncagent/obsidian/obsidian.go`
- `.omo/evidence/task-7-cleanup-scan.txt`
- `.omo/evidence/task-7-debug-audit.txt`
- `.omo/evidence/task-7-go-sync-cli-migration-conflict.txt`
- `.omo/evidence/task-7-go-sync-cli-migration-engine.txt`
- `.omo/evidence/task-7-go-test-all.txt`
- `.omo/evidence/task-7-go-test-engine.txt`
- `.omo/evidence/task-7-go-vet-engine.txt`
- `.omo/evidence/task-7-red.txt`
- `.omo/evidence/task-7-review-work.txt`
- `.omo/evidence/task-7-size-check.txt`

## verificationCommands

- `go test -count=1 ./internal/syncagent/engine` -> PASS
- `go test -count=1 ./internal/syncagent/engine -run TestRunSyncCreateUpdateDelete -v` -> PASS
- `go test -count=1 ./internal/syncagent/engine -run TestRunSyncConflictDoesNotAutoMerge -v` -> PASS
- `go vet ./internal/syncagent/engine` -> PASS
- `go test ./...` -> PASS, mostly cached
- `go test -count=1 ./...` -> PASS, uncached stronger check
- `go test -race ./internal/syncagent/engine` -> PASS, cached
- `go test -race -count=1 ./internal/syncagent/engine` -> PASS, uncached stronger check
- `go test -shuffle=on -count=1 ./internal/syncagent/engine` -> PASS
- `gofmt -l internal/syncagent/engine/*.go` -> PASS, no output

## scopeReview

- Current dirty worktree is limited to `internal/syncagent/engine/**` and `.omo/evidence/task-7-*`, plus this gate-review artifact.
- No Python source/tests/docs, plan checkboxes, ledger, cmd/config/client/scanner/rules/conflict/obsidian files are modified in the current Task 7 dirty set.
- `internal/syncagent/engine/doc.go` has the only tracked diff: one package comment line.

## behaviorReview

- Manifest validation/load/save: source confirms load/validate in `engine.go:15-32`; save in `engine.go:31`.
- Device registration: source confirms `RegisterDevice` in `engine.go:71-77`; dry-run test confirms no registration in dry-run.
- First pull: source confirms `pull(..., "")` in `engine.go:78`.
- Same-run pull-conflict push skip: source confirms skip map from `summary.Conflicts` in `engine.go:81-90`; test covers no PUT.
- Scan: source confirms `scanner.ScanVault` in `engine.go:85`.
- Push create/update/delete base revisions: source and focused test cover `0`, manifest revision, and delete revision.
- Resolved conflict push: source uses manifest conflict server revision; test covers PUT base revision 2 and conflict cleanup.
- Server-delete acceptance: source and test cover local deletion accepting `ServerDeleted` conflict without server DELETE.
- Second pull with device_id: source confirms `pull(..., cfg.DeviceID)` and test records `dev`.
- Dry-run no writes/pushes: test covers no server writes, no pulled local file, no manifest save in a new temp vault.
- No auto-merge conflicts: source writes conflict files rather than overwriting dirty local files; focused test covers original content preserved.
- Obsidian refresh summary: source confirms summary fields/warnings are set in `runObsidian`; Task 7 tests do not directly assert this integration.
- Status report: source and test cover server status, local cursor, tracked file count, and `device_id` query.

## adversarialClasses

- malformed_input: unsafe server paths are rejected in `vaultPath`/`applyChange`; manifest-vault mismatch returns `ErrSync`. No new malformed-manifest test in Task 7 because manifest package owns that behavior.
- stale_state: verified worktree path and branch directly; tests were rerun after evidence inspection.
- dirty_worktree: current dirty set inspected; limited to Task 7 engine/evidence plus this review artifact.
- misleading_success_output: executor evidence was not trusted; required commands were rerun, plus uncached full suite and uncached race.
- flaky_tests: `go test -shuffle=on -count=1 ./internal/syncagent/engine` and race pass.
- hung_long_commands: all commands completed within the tool timeouts.

## exactEvidenceGaps

- No notepad path was supplied in the user input or found for Task 7.
- No Task 7 evidence file explicitly provides a manual QA matrix beyond focused Go test transcripts.
- `.omo/evidence/task-7-review-work.txt` does not document the required skill-perspective coverage for `remove-ai-slops` and `programming`.
- Task 7 tests do not directly assert the engine-level Obsidian refresh callback/summary path, although source inspection confirms it is implemented.

## final

REJECT
