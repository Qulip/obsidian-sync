# F4 Scope Fidelity Gate Review

recommendation: REJECT

## originalIntent

Final Verification Wave F4 for the Go sync CLI migration: compare the Go
`obsidian-sync-agent` behavior against `docs/sync-agent.md`, `README.md`, and
the Python sync-agent source/tests. F4 may pass only if config/env/manifest/
conflict/ignore/exit-code contracts are unchanged or intentional deviations
have explicit user approval.

## desiredOutcome

From the user's perspective, the shipped Go binary should be a behavior-compatible
replacement local sync client while the Python server and Python CLI remain
available. Users should not see silent changes to required commands, documented
flags, config/env names, manifest/conflict files, ignore rules, or exit-code
meanings.

## userOutcomeReview

The main sync behavior is well supported by Go tests and Task 8 live QA, but F4
does not satisfy the unchanged-contract bar. Direct CLI comparison found an
unapproved exit-code change for invoking the binary without a subcommand, and an
unapproved help surface change away from the Python-characterized double-dash
flag help. Under the loaded `programming` and `remove-ai-slops` criteria, the
branch also still carries unreviewed production helper parameter-bloat slop.

## blockers

1. **Unapproved root invocation exit-code change.**
   - Python command: `uv run obsidian-sync-agent` -> exit code 2.
   - Go command: `.omo/evidence/obsidian-sync-agent` -> exit code 0.
   - Python stderr: `the following arguments are required: command`.
   - Go behavior: prints root help and succeeds.
   - No explicit user approval for this exit-code change was found.

2. **Unapproved help contract drift.**
   - Python `sync --help` prints `--vault-root`, `--vault-id`, `--server`,
     `--device-id`, `--verbose`, `--dry-run`, and
     `--require-obsidian-refresh`.
   - Go `sync --help` prints `-vault-root`, `-vault-id`, `-server`,
     `-device-id`, `-verbose`, `-dry-run`, and
     `-require-obsidian-refresh`.
   - `tests/sync_agent/test_contract.py` explicitly characterizes the Python
     help with double-dash flag substrings. No explicit approval for changing
     the user-visible help surface was found.

3. **Unresolved programming/remove-ai-slops slop.**
   - Direct scan found production helper signatures above the `programming`
     skill's >3-parameter smell threshold:
     `cmd/obsidian-sync-agent/main.go:119`,
     `internal/syncagent/conflict/conflict.go:89`,
     `internal/syncagent/obsidian/obsidian.go:104`,
     `internal/syncagent/obsidian/obsidian.go:114`,
     `internal/syncagent/obsidian/obsidian.go:145`, and
     `internal/syncagent/client/client.go:151`.
   - Later reports justify some test-helper arity, but not all of these
     production helpers. This fails the required slop/programming pass.

4. **Evidence gaps.**
   - `uv run pytest -p no:cacheprovider tests/sync_agent` exits 4 before
     collection because `tests/conftest.py` requires a missing `.env`/database
     URL.
   - `.omo/evidence/go-sync-cli-migration-task-3-gate-review.md` remains a
     REJECT report for missing Task 3 report coverage. The later
     `.omo/evidence/task-3-review-and-qa-matrix.txt` exists and was inspected,
     but no later approving Task 3 gate artifact was found.

## contractComparison

- Config/env: Go matches the documented/Python env names and precedence for
  server, vault id, vault root, device id, device name, token env-only,
  Obsidian key, and require-refresh.
- Manifest: Go preserves the documented JSON shape and local path.
- Conflict: Go preserves filename shape, metadata sections, deleted
  placeholders, and duplicate reuse by device/server revision.
- Ignore/scanner: Go preserves markdown-only sync, hidden path exclusion,
  Obsidian/sync-agent/trash exclusions, and conflict-file exclusions.
- Exit codes: normal success/dry-run/conflict/config-error/refresh-failure
  paths are covered, but root no-subcommand behavior changed from Python exit 2
  to Go exit 0.

## commandExitCodes

- `go test -count=1 ./cmd/obsidian-sync-agent ./internal/syncagent/...`:
  exit 0.
- `uv run pytest -p no:cacheprovider tests/sync_agent`: exit 4.
- `uv run obsidian-sync-agent`: exit 2.
- `.omo/evidence/obsidian-sync-agent`: exit 0.
- `uv run obsidian-sync-agent --help`: exit 0.
- `.omo/evidence/obsidian-sync-agent --help`: exit 0.
- `uv run obsidian-sync-agent sync --help`: exit 0.
- `.omo/evidence/obsidian-sync-agent sync --help`: exit 0.
- `uv run obsidian-sync-agent status --help`: exit 0.
- `.omo/evidence/obsidian-sync-agent status --help`: exit 0.
- `git diff --check main...HEAD`: exit 2, due evidence-artifact whitespace.

## checkedArtifactPaths

- `README.md`
- `docs/sync-agent.md`
- `.omo/plans/go-sync-cli-migration.md`
- `src/obsidian_sync/sync_agent/config.py`
- `src/obsidian_sync/sync_agent/cli.py`
- `src/obsidian_sync/sync_agent/manifest.py`
- `src/obsidian_sync/sync_agent/conflict.py`
- `src/obsidian_sync/sync_agent/ignore.py`
- `src/obsidian_sync/sync_agent/scanner.py`
- `src/obsidian_sync/sync_agent/engine.py`
- `src/obsidian_sync/sync_agent/client.py`
- `tests/sync_agent/test_contract.py`
- `tests/sync_agent/test_config.py`
- `tests/sync_agent/test_manifest.py`
- `tests/sync_agent/test_conflict.py`
- `tests/sync_agent/test_ignore.py`
- `tests/sync_agent/test_scanner.py`
- `tests/test_sync_agent_integration.py`
- `cmd/obsidian-sync-agent/main.go`
- `cmd/obsidian-sync-agent/main_test.go`
- `internal/syncagent/config/doc.go`
- `internal/syncagent/manifest/manifest.go`
- `internal/syncagent/rules/rules.go`
- `internal/syncagent/scanner/scanner.go`
- `internal/syncagent/conflict/conflict.go`
- `internal/syncagent/client/client.go`
- `internal/syncagent/engine/engine.go`
- `internal/syncagent/engine/pull.go`
- `internal/syncagent/engine/push.go`
- `internal/syncagent/obsidian/obsidian.go`
- `.omo/evidence/task-8-go-sync-cli-migration-live-sync.txt`
- `.omo/evidence/go-sync-cli-migration-task-8-regate-review.md`
- `.omo/evidence/go-sync-cli-migration-task-9-regate-review.md`

## exactEvidenceGaps

- No approval artifact for root no-command exit-code drift.
- No approval artifact for help-output drift from double-dash Python help to
  single-dash Go help.
- Python sync-agent test execution is blocked by missing `.env`/database setup.
- No approving Task 3 gate artifact was found after the recorded Task 3 REJECT.

