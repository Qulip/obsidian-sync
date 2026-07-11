# Global Review Lane 3 - Code Quality

Status: FAIL
codeQualityStatus: BLOCK
recommendation: REQUEST_CHANGES

Worktree: `/Users/ia03060_mac/workspace/qulip/obsidian-sync-go-cli-worktree`
Branch: `work/go-sync-cli-migration`
Base: `main` merge-base `db44fcfe3e7865c109c9be9869f3b279fdbf15ab`

## Skill Perspective Check

Required skill-perspective check ran before judging tests and maintainability.

- Loaded `remove-ai-slops` from `/Users/ia03060_mac/.codex/plugins/cache/sisyphuslabs/omo/4.16.0/skills/remove-ai-slops/SKILL.md`.
- Loaded `programming` from `/Users/ia03060_mac/.codex/plugins/cache/sisyphuslabs/omo/4.16.0/skills/programming/SKILL.md`.
- Loaded Go-specific programming references:
  - `references/go/README.md`
  - `references/go/cobra-stack.md`
  - `references/go/error-handling.md`
  - `references/go/testing.md`
  - `references/code-smells.md`

Result:

- `remove-ai-slops` perspective violation: yes. `cmd/obsidian-sync-agent/help_test.go` locks command help to `stderr`, matching the current implementation rather than the CLI compatibility contract. This is an overfit/implementation-mirroring test around the final F4 help fix.
- `programming` perspective violation: yes. The CLI is manually parsed and manually renders duplicated flag help despite the Go CLI reference preferring Cobra/pflag for multi-command CLIs. The more concrete issue is that this duplication already produced a user-visible stream mismatch.

## Commands Run

- `git status --short --branch`: exit 0. Initial status showed branch `work/go-sync-cli-migration` plus untracked `.omo/evidence/global-review-qa-build.txt` and `.omo/evidence/global-review-qa-noargs.txt`.
- `git diff --name-only main...HEAD`: exit 0. Reported 179 changed paths, including Go source/tests, docs, Makefile, `.omo` evidence, and built binaries.
- `git diff --stat main...HEAD`: exit 0. Reported 179 files changed, 10641 insertions, 6 deletions, plus binary artifacts.
- `go test -count=1 ./...`: exit 0.
- `go vet ./...`: exit 0.
- Pure LOC scan over changed Go files: exit 0. Highest quick-scan results: `cmd/obsidian-sync-agent/main_test.go` 250, `internal/syncagent/client/client.go` 247, `internal/syncagent/config/config_test.go` 246, `internal/syncagent/engine/engine_test.go` 243.
- Arity scan over changed Go files: exit 0. Found only three 4-parameter test helpers: `cmd/obsidian-sync-agent/main_test.go:266`, `internal/syncagent/scanner/scanner_test.go:79`, `internal/syncagent/client/client_test.go:255`.
- `go run ./cmd/obsidian-sync-agent`: command exit 1 because `go run` wraps nonzero child exits; output included `exit status 2`, confirming the child no-arg path exits 2.
- `go run ./cmd/obsidian-sync-agent --help`: exit 0.
- `go run ./cmd/obsidian-sync-agent sync --help 1>/dev/null`: exit 0 and printed help to stderr.
- `go run ./cmd/obsidian-sync-agent sync --help 2>/dev/null`: exit 0 and printed no stdout.
- `go run ./cmd/obsidian-sync-agent status --help 1>/dev/null`: exit 0 and printed help to stderr.
- `go test -count=1 ./cmd/obsidian-sync-agent -run 'TestRun_returnsError_whenNoCommandSupplied|TestRun_printsDoubleDashHelp_whenCommandHelpRequested' -v`: exit 0.
- Supplemental: `uv run pytest tests/sync_agent/test_contract.py`: exit 4. It did not reach tests because `tests/conftest.py` requires a local `.env` file that is absent in this worktree.

## Findings

### CRITICAL

None.

### HIGH

1. Explicit subcommand help exits 0 but writes to stderr instead of stdout.

   Evidence:

   - `cmd/obsidian-sync-agent/main.go:74` and `cmd/obsidian-sync-agent/main.go:95` pass `stderr` into `parseCommand`.
   - `cmd/obsidian-sync-agent/main.go:130-133` sets the flag output to that writer and renders usage through it.
   - `cmd/obsidian-sync-agent/help_test.go:88-99` asserts subcommand help by reading `stderr`, so the test locks the implementation behavior.
   - `tests/sync_agent/test_contract.py:27-33` captures Python argparse help from `stdout` for the compatibility contract.
   - The migration plan recorded `uv run obsidian-sync-agent sync --help > .omo/evidence/task-1-go-sync-cli-migration-help.txt`, which only captures stdout.
   - Direct check: `go run ./cmd/obsidian-sync-agent sync --help 1>/dev/null` still printed help; `go run ./cmd/obsidian-sync-agent sync --help 2>/dev/null` printed nothing.

   Why this blocks approval: F4 specifically touched double-dash help rendering, but the resulting behavior is not compatible with normal explicit-help CLI semantics or the Python contract. It also demonstrates an overfit test: the test proves flags appear somewhere, not that help is delivered on the correct stream.

2. Push-conflict handling masks any server file fetch failure as "server deleted".

   Evidence:

   - `internal/syncagent/engine/push.go:139-146` handles a PUT/DELETE conflict by calling `GetFile`; if `GetFile` returns any error, `serverContent` remains `conflict.ServerDeletedPlaceholder`.
   - `internal/syncagent/client/errors.go:16-20` exposes `APIError.StatusCode`, so this code can distinguish a true 404/not-found style condition from network, auth, malformed response, or server errors.
   - Existing coverage `internal/syncagent/engine/engine_test.go:175-190` only covers the successful server-content fetch path.
   - `internal/syncagent/engine/fake_client_test.go:77-81` returns a generic `errors.New("missing fake file")` for absent fake content; no test asserts that this should become an error instead of a deleted placeholder.

   Why this blocks approval: a transient or authorization failure while fetching the server version will be reported as a normal conflict with "(file is deleted on the server)", returning the conflict path instead of surfacing a sync error. That can mislead users during manual conflict resolution and hides an operational failure behind a false conflict artifact.

### MEDIUM

1. Product build output and user docs are tied to `.omo/evidence/builds`, and binary artifacts are included in the branch diff.

   Evidence:

   - `Makefile:4` sets `BUILD_DIR := .omo/evidence/builds`.
   - `Makefile:7-17` builds the installable agent and cross-platform binaries into that evidence directory.
   - `docs/sync-agent.md:16-28` instructs users to build/install from `.omo/evidence/builds`.
   - `git diff --name-only main...HEAD` includes five platform binaries under `.omo/evidence/builds/` plus `.omo/evidence/obsidian-sync-agent`.

   Risk: evidence folders are workflow artifacts, not a stable product distribution path. Keeping built binaries in the branch also makes review and repository history heavier than necessary. If this project intentionally commits `.omo` evidence, the source/docs should still not make `.omo/evidence/builds` the user-facing install path.

2. The CLI parser and help rendering duplicate flag metadata by hand.

   Evidence:

   - Flags are registered at `cmd/obsidian-sync-agent/main.go:135-143`.
   - The same flag names and descriptions are manually re-rendered at `cmd/obsidian-sync-agent/main.go:158-175`.

   Risk: the current tests cover the present flag set, but any future flag change must update both registration and help text. This duplication is the root cause of the help-stream overfit and is less idiomatic than using Cobra/pflag's help model for a multi-command CLI.

3. Several new files are already in the 200-250 pure-LOC warning band.

   Evidence:

   - `cmd/obsidian-sync-agent/main_test.go`: 250 quick-scan pure LOC.
   - `internal/syncagent/client/client.go`: 247.
   - `internal/syncagent/config/config_test.go`: 246.
   - `internal/syncagent/engine/engine_test.go`: 243.
   - `internal/syncagent/client/client_test.go`: 235.
   - `cmd/obsidian-sync-agent/main.go`: 216.

   Risk: no file exceeded the strict `>250` defect threshold in the quick scan, but the next substantive edit to these files should split by responsibility before adding more code.

### LOW

1. CLI execution uses `context.Background()` instead of a signal-cancelable command context.

   Evidence:

   - `cmd/obsidian-sync-agent/main.go:86` calls `engine.RunStatus(context.Background(), ...)`.
   - `cmd/obsidian-sync-agent/main.go:106` calls `engine.RunSync(context.Background(), ...)`.

   Risk: the engine and client APIs accept context, but the CLI entrypoint cannot propagate SIGINT/SIGTERM cancellation through in-flight HTTP and filesystem work. The Go CLI reference recommends `signal.NotifyContext` at the main boundary.

2. Some lower-level helpers return or repackage raw errors with limited context.

   Evidence examples:

   - `internal/syncagent/atomicfile/atomicfile.go:10-32` returns raw filesystem errors.
   - `internal/syncagent/scanner/scanner.go:29-69` returns raw path/walk/hash errors.
   - `internal/syncagent/config/doc.go:167-197` formats underlying errors with `%v` inside `ConfigError`, losing the original error chain.

   Risk: not a current functional blocker because higher layers add some context, but it falls short of the Go error-handling reference's `%w`/chain-preserving guidance and will make support/debugging harder.

## Test Relevance Review

- Go tests are broad and mostly behavioral: config precedence, client envelopes, path encoding, conflict files, scanner rules, dry-run, sync push/pull/delete/conflict, status, and final F4 no-arg/help cases.
- The final F4 help test is not adequate because it mirrors the current stderr implementation rather than the compatibility/user contract.
- The push-conflict error path is missing coverage for `GetFile` failure after a conflict response.
- No deletion-only tests or tests that merely verify requested removal were found in the changed Go tests.
- Helper arity issues are confined to three test helpers; production helper arity refactors in client/conflict/obsidian are acceptable.

## Scope And Maintainability Notes

- The Go source is reasonably decomposed into `cmd/obsidian-sync-agent` and `internal/syncagent/*`.
- There are no changed production Go files over the strict `>250` pure-LOC ceiling by quick scan.
- The branch diff is noisy because `.omo` plans/evidence and built binaries are included. Reviewers should not treat previous evidence reports as trusted; the commands above were re-run directly.
- The supplemental Python contract test did not run in this environment because `.env` is missing. This is reported as evidence only; the required Go gates passed.

## Blockers

1. Fix explicit subcommand `--help` to write to stdout on exit 0, and update the F4 test so it asserts the correct stream instead of implementation-mirroring stderr behavior.
2. Fix `pushConflict` so only a confirmed server-deleted/not-found response uses `ServerDeletedPlaceholder`; other `GetFile` failures must return a wrapped sync error and be covered by a regression test.

## Final Recommendation

REQUEST_CHANGES. The Go gates pass, but the branch should not be approved until the two HIGH findings are fixed and verified.
