recommendation: REJECT

blockers:
- Missing current F4 blocker-fix code-review/manual-QA matrix coverage. Direct
  review found the behavior fixes are present, but the supplied F4 fix artifact
  `.omo/evidence/f4-fix-contract-and-slop.txt` is a command/arity transcript,
  not a code-review report that explicitly covers the required
  `programming` plus `remove-ai-slops`/overfit criteria for the current F4 fix
  diff. The available explicit coverage in `.omo/evidence/final-f2-code-quality*`
  and task review matrices predates or does not cover the F4 blocker-fix changes
  to `cmd/obsidian-sync-agent/main.go`, `cmd/obsidian-sync-agent/help_test.go`,
  `internal/syncagent/client/client.go`, `internal/syncagent/conflict/conflict.go`,
  and `internal/syncagent/obsidian/obsidian.go`.

originalIntent:
- Re-run Final Verification Wave F4 after fixes for the prior F4 blockers.
- Compare the Go `obsidian-sync-agent` behavior against `docs/sync-agent.md`,
  `README.md`, and Python sync-agent source/tests.
- Pass only if config/env/manifest/conflict/ignore/exit-code contracts are
  unchanged, or intentional deviations have explicit approval.

desiredOutcome:
- Users receive a Go `obsidian-sync-agent` binary that preserves the existing
  Python sync-agent user contract: required subcommands, double-dash flags,
  config/env precedence, manifest JSON shape, conflict naming/content, ignore
  behavior, and exit-code meanings.
- The Python FastAPI server and Python CLI remain available; no Python
  production code is changed by the Go client migration.

userOutcomeReview:
- Direct behavior checks support the F4 blocker fixes:
  - Go no-subcommand invocation now exits 2, matching Python.
  - Go `sync --help` and `status --help` now show the expected double-dash
    flags.
  - Production arity scan over the requested Go paths has no production
    function with more than 3 explicit params.
  - `internal/syncagent/client/client.go` is 247 pure LOC, below the 250 pure
    LOC ceiling.
  - Working diff has no Python file changes; branch diff has no
    `src/obsidian_sync` changes and only the planned Python characterization
    test `tests/sync_agent/test_contract.py`.
- F4 still cannot be approved because final-gate evidence completeness is
  missing for the current F4 fix diff. The final gate requires an explicit
  report-backed skill-perspective and overfit/slop review, not only a direct
  reviewer pass.

requiredCommandExitCodes:
- `go test -count=1 ./cmd/obsidian-sync-agent ./internal/syncagent/...`:
  exit 0.
- `go test -count=1 ./...`: exit 0.
- `go vet ./...`: exit 0.
- `go build -o .omo/evidence/obsidian-sync-agent ./cmd/obsidian-sync-agent`:
  exit 0.
- `uv run obsidian-sync-agent`: exit 2.
- `.omo/evidence/obsidian-sync-agent`: exit 2.
- `uv run obsidian-sync-agent sync --help`: exit 0.
- `.omo/evidence/obsidian-sync-agent sync --help`: exit 0.
- `uv run obsidian-sync-agent status --help`: exit 0.
- `.omo/evidence/obsidian-sync-agent status --help`: exit 0.
- Production arity parser scan excluding `_test.go` files in
  `cmd/obsidian-sync-agent`, `internal/syncagent/conflict`,
  `internal/syncagent/obsidian`, and `internal/syncagent/client`: exit 0.
- `git diff --check`: exit 0.

additionalVerification:
- `uv run pytest tests/sync_agent tests/test_sync_rules.py`: exit 4. Collection
  is blocked before tests run because `tests/conftest.py` reads a missing
  `.env` file and no database URL is configured. This matches the known
  environment limitation.
- `uv run python -m unittest discover -s tests/sync_agent -p 'test_contract.py'`:
  exit 0. This directly exercised the Python contract characterization file
  without importing the DB-bound pytest conftest.
- Pure LOC:
  - `cmd/obsidian-sync-agent/main.go`: 216.
  - `cmd/obsidian-sync-agent/help_test.go`: 87.
  - `internal/syncagent/conflict/conflict.go`: 170.
  - `internal/syncagent/obsidian/obsidian.go`: 187.
  - `internal/syncagent/client/client.go`: 247.
- Python scope:
  - `git diff --name-only -- '*.py'`: no output.
  - `git diff --name-only main...HEAD -- 'src/obsidian_sync/**'`: no output.
  - `git diff --name-status main...HEAD -- '*.py'`: `A tests/sync_agent/test_contract.py`.

contractComparison:
- Config/env: Source inspection found the documented environment variables and
  CLI/config precedence preserved between Python `config.py`, Go config usage,
  README, and `docs/sync-agent.md`.
- Manifest: Python and Go manifest types preserve `vault_id`, `device_id`,
  `last_sync_cursor`, `files`, and `conflicts` JSON keys and conflict metadata.
- Conflict: Python and Go conflict files preserve filename shape, metadata
  headings, local/server sections, deleted placeholders, and duplicate reuse by
  device plus server revision.
- Ignore/scanner: Python and Go preserve Markdown-only sync, hidden path
  exclusion, `.obsidian`, `.obsidian-sync-agent`, `.trash`, conflict file, and
  external sync-conflict exclusions.
- Client/HTTP: Go preserves endpoint path construction, per-segment path
  encoding, Bearer token header behavior, JSON request bodies, envelope parsing,
  and `SYNC_CONFLICT` typed error behavior.
- Obsidian: Go preserves disabled-by-default behavior, health probe, optional
  reload command, API-key env fallback, TLS verify option, and require-refresh
  failure semantics.
- Exit codes: Direct probes and source inspection show the user-visible codes
  match the documented Python contract for success/dry-run, conflicts, config
  or sync error, and required Obsidian refresh failure.

directSlopAndProgrammingPass:
- Loaded and applied local `remove-ai-slops` and `programming` criteria.
- No unresolved production arity blocker remains in the requested paths.
- No oversized changed source file exceeds 250 pure LOC.
- No deletion-only tests, tests merely proving a requested removal, tautological
  tests, or implementation-mirroring tests were found in the F4 fix test file.
- The new small structs (`commandSpec`, `existingConflictQuery`, `bodyRequest`,
  `jsonRequest`, `obsidianSession`) are bounded typed groupings for related
  arguments or receiver context and do not introduce a broad framework or
  behavior-changing extraction.

checkedArtifactPaths:
- `.omo/plans/go-sync-cli-migration.md`
- `.omo/evidence/f4-fix-contract-and-slop.txt`
- `.omo/evidence/go-sync-cli-migration-task-3-final-approval-note.md`
- `.omo/evidence/final-f4-scope-fidelity-gate-review.md`
- `.omo/evidence/final-f4-scope-fidelity.txt`
- `.omo/evidence/final-f2-code-quality-gate-review.md`
- `.omo/evidence/final-f2-code-quality.txt`
- `.omo/evidence/final-f3-real-manual-qa.txt`
- `.omo/evidence/task-3-review-and-qa-matrix.txt`
- `.omo/evidence/task-8-review-and-qa-matrix.txt`
- `.omo/evidence/go-sync-cli-migration-task-8-regate-review.md`
- `.omo/evidence/task-9-review-and-qa-matrix.txt`
- `.omo/evidence/obsidian-sync-agent`
- `README.md`
- `docs/sync-agent.md`
- `tests/conftest.py`
- `tests/sync_agent/test_contract.py`
- `src/obsidian_sync/sync_agent/cli.py`
- `src/obsidian_sync/sync_agent/config.py`
- `src/obsidian_sync/sync_agent/manifest.py`
- `src/obsidian_sync/sync_agent/ignore.py`
- `src/obsidian_sync/sync_agent/conflict.py`
- `src/obsidian_sync/sync_agent/client.py`
- `src/obsidian_sync/sync_agent/obsidian.py`
- `cmd/obsidian-sync-agent/main.go`
- `cmd/obsidian-sync-agent/help_test.go`
- `internal/syncagent/manifest/manifest.go`
- `internal/syncagent/rules/rules.go`
- `internal/syncagent/scanner/scanner.go`
- `internal/syncagent/conflict/conflict.go`
- `internal/syncagent/client/client.go`
- `internal/syncagent/obsidian/obsidian.go`

exactEvidenceGaps:
- No current F4 blocker-fix code-review report or manual-QA matrix was supplied
  that explicitly covers the `programming` and `remove-ai-slops`/overfit
  criteria for the F4 fix diff.
- Python pytest characterization remains environment-blocked by missing
  `.env`/database setup, although direct source inspection and unittest
  execution of `tests/sync_agent/test_contract.py` pass.
- No external notepad path was provided.

final:
- Behavioral blocker fixes are verified, but F4 final approval is rejected on
  the missing current code-review/overfit-slop coverage artifact required by
  the final gate.
