recommendation: REJECT

blockers:
- Dirty/stale-state blocker: `git status --short` shows the claimed Go implementation and evidence as untracked, plus unrelated later-task directories. `git merge-base HEAD main` equals `HEAD`, and `git diff main...HEAD` is empty, so there is no committed/reviewable branch diff for Task 4.
- Missing final-gate artifacts: no Task 4 code review report, manual QA matrix, or notepad path was present. The only Task 4 review-like artifact is `.omo/evidence/task-4-cleanup-receipt.txt`, and it states review-work orchestration was not runnable; it does not provide the required skill-perspective/overfit/slop coverage.
- Malformed manifest blocker: `internal/syncagent/manifest/manifest.go` silently treats invalid non-JSON text as an empty manifest when the first non-space byte is not `{` (`Load`, lines 50-56). Python `load_manifest` calls `json.loads` first and only returns empty for valid non-dict JSON, so corrupt text such as `not-json` should not be accepted as an empty manifest. Existing tests cover `[]` but not invalid non-JSON.

originalIntent:
Implement Task 4 of the Go sync CLI migration: manifest persistence, atomic file writes, Python-compatible sync ignore rules, and vault scanning/classification.

desiredOutcome:
Task 4 packages should be present in a reviewable diff, use only the Go standard library, pass the required package `go test` and `go vet` commands, preserve Python contract behavior for manifest/rules/scanner behavior, and include supporting evidence artifacts.

userOutcomeReview:
The required package tests, `go vet`, `gofmt -l`, and `go list -m all` pass from the current filesystem. However, the user-visible outcome is not confirmed because the work is untracked rather than represented by a branch diff, required review artifacts are missing, and manifest corruption can be silently accepted as empty state.

checkedArtifactPaths:
- `go.mod`
- `internal/syncagent/atomicfile/atomicfile.go`
- `internal/syncagent/atomicfile/atomicfile_test.go`
- `internal/syncagent/manifest/manifest.go`
- `internal/syncagent/manifest/manifest_test.go`
- `internal/syncagent/rules/rules.go`
- `internal/syncagent/rules/rules_test.go`
- `internal/syncagent/scanner/scanner.go`
- `internal/syncagent/scanner/scanner_test.go`
- `src/obsidian_sync/sync_agent/atomic.py`
- `src/obsidian_sync/sync_agent/manifest.py`
- `src/obsidian_sync/sync_agent/ignore.py`
- `src/obsidian_sync/sync_agent/scanner.py`
- `src/obsidian_sync/domain/sync_rules.py`
- `tests/sync_agent/test_contract.py`
- `.omo/evidence/task-4-changed-files.txt`
- `.omo/evidence/task-4-red.txt`
- `.omo/evidence/task-4-go-test.txt`
- `.omo/evidence/task-4-go-test.exitcode`
- `.omo/evidence/task-4-go-vet.txt`
- `.omo/evidence/task-4-go-vet.exitcode`
- `.omo/evidence/task-4-git-status.txt`
- `.omo/evidence/task-4-go-sync-cli-migration-scanner-manifest.txt`
- `.omo/evidence/task-4-pure-loc.txt`
- `.omo/evidence/task-4-cleanup-receipt.txt`

verifiedCommands:
- `git status --short` -> dirty/untracked Task 4 and unrelated later-task files.
- `git diff main...HEAD` -> empty.
- `go test -count=1 ./internal/syncagent/manifest ./internal/syncagent/atomicfile ./internal/syncagent/rules ./internal/syncagent/scanner` -> exit 0.
- `go vet ./internal/syncagent/manifest ./internal/syncagent/atomicfile ./internal/syncagent/rules ./internal/syncagent/scanner` -> exit 0.
- `gofmt -l internal/syncagent/manifest/manifest.go internal/syncagent/manifest/manifest_test.go internal/syncagent/atomicfile/atomicfile.go internal/syncagent/atomicfile/atomicfile_test.go internal/syncagent/rules/rules.go internal/syncagent/rules/rules_test.go internal/syncagent/scanner/scanner.go internal/syncagent/scanner/scanner_test.go` -> exit 0, no output.
- `go list -m all` -> only `github.com/Qulip/obsidian-sync`.
- `go test -race -shuffle=on -count=1 ./internal/syncagent/manifest ./internal/syncagent/atomicfile ./internal/syncagent/rules ./internal/syncagent/scanner` -> exit 0.
- `go test -count=1 ./...` -> exit 0.
- `go vet ./...` -> exit 0.

exactEvidenceGaps:
- No Task 4 code review report artifact found under `.omo/evidence/`.
- No Task 4 manual QA matrix artifact found under `.omo/evidence/`.
- No notepad path was provided in the prompt or found in the evidence list.
- No committed diff exists for Task 4; all implementation files are untracked in the worktree.
- Tests do not cover invalid non-JSON manifest content that starts with a non-`{` byte.
