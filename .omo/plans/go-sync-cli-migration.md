# go-sync-cli-migration - Work Plan

## TL;DR (For humans)
**What you'll get:** A standalone sync client binary that can be built for macOS, Linux, and Windows, while the existing Python API server remains unchanged. The migration is staged so the current Python client stays available until the Go client proves behavioral parity.

**Why this approach:** The service already has a clear client/server boundary, and the pain is client distribution rather than server runtime. Keeping the server in Python and moving only the local sync client to Go gives cross-platform binaries without widening the rewrite.

**What it will NOT do:** It will not rewrite the FastAPI server. It will not change existing sync data contracts, manifest format, config names, conflict format, or exit-code meanings. It will not remove the Python client until the Go client passes parity checks.

**Effort:** Large
**Risk:** Medium - sync correctness depends on preserving manifest, conflict, and optimistic-revision behavior exactly.
**Decisions to sanity-check:** Root Go module; `cmd/obsidian-sync-agent` plus `internal/syncagent/*`; standard-library CLI parsing first; Go client introduced alongside Python client before replacement.

Your next move: run `$start-work` or ask for a high-accuracy plan review first. Full execution detail follows below.

---

> TL;DR (machine): Large/medium-risk staged Go port of the local `obsidian-sync-agent` client, preserving Python server and current sync contracts.

## Scope
### Must have
- Preserve Python server/API code under `src/obsidian_sync` and migrate only the local sync-agent client behavior.
- Add root Go module files: `go.mod`, later `go.sum` only if dependencies are introduced.
- Add Go command entry point: `cmd/obsidian-sync-agent/main.go`.
- Add private implementation packages under `internal/syncagent/`:
  - `config`: CLI/env/config-file precedence, device ID sanitization, Obsidian config.
  - `manifest`: `.obsidian-sync-agent/manifest.json` load/save and atomic write.
  - `scanner`: Markdown-only vault scan, SHA-256 hashing, local change classification.
  - `rules`: ignore/conflict path predicates matching Python domain/sync-agent rules.
  - `client`: HTTP client for sync API envelope, DTOs, URL path encoding, conflict errors.
  - `engine`: pull -> scan -> push -> pull orchestration, dry-run, status, summary.
  - `conflict`: conflict filename/content generation and duplicate prevention.
  - `obsidian`: optional Obsidian Local REST API health/reload behavior.
  - `atomicfile`: write temp file then rename.
- Preserve current commands and flags:
  - `obsidian-sync-agent sync [--vault-root] [--vault-id] [--server] [--device-id] [--verbose] [--dry-run] [--require-obsidian-refresh]`
  - `obsidian-sync-agent status [--vault-root] [--vault-id] [--server] [--device-id] [--verbose]`
- Preserve config precedence: CLI > environment > config file > default.
- Preserve environment variables: `OBSIDIAN_SYNC_AGENT_SERVER`, `OBSIDIAN_SYNC_AGENT_VAULT_ID`, `OBSIDIAN_SYNC_AGENT_VAULT_ROOT`, `OBSIDIAN_SYNC_AGENT_DEVICE_ID`, `OBSIDIAN_SYNC_AGENT_DEVICE_NAME`, `OBSIDIAN_SYNC_AGENT_TOKEN`, `OBSIDIAN_LOCAL_REST_API_KEY`.
- Preserve local files:
  - `{vault_root}/.obsidian-sync-agent/config.json`
  - `{vault_root}/.obsidian-sync-agent/manifest.json`
- Preserve manifest JSON shape and conflict metadata shape.
- Preserve exit codes:
  - `0`: success or dry-run
  - `1`: sync completed with conflicts
  - `2`: config error or sync failure
  - `3`: required Obsidian refresh failed
- Build and verify standalone binaries for at least:
  - `darwin/arm64`
  - `darwin/amd64`
  - `linux/amd64`
  - `linux/arm64`
  - `windows/amd64`

### Must NOT have (guardrails, anti-slop, scope boundaries)
- Do not rewrite the FastAPI app, API routes, SQLAlchemy models, repositories, migrations, MCP server, or vectorizing flow.
- Do not remove `src/obsidian_sync/sync_agent/*` or the Python `obsidian-sync-agent` console script in this migration.
- Do not change server endpoint paths, request/response JSON field names, response-envelope expectations, auth header behavior, or DELETE-with-body behavior.
- Do not change manifest JSON keys, conflict file naming/content, ignored paths, Markdown-only v1 policy, or token env-only policy.
- Do not introduce a Go CLI framework unless the standard library becomes materially insufficient during implementation.
- Do not store API tokens in config files, logs, manifest files, or QA evidence.

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: TDD for Go packages plus compatibility characterization before porting risky behavior.
- Python regression commands:
  - `uv run pytest tests/sync_agent tests/test_sync_rules.py`
  - `uv run pytest tests/test_sync_api.py tests/test_sync_agent_integration.py` when local PostgreSQL is available.
- Go verification commands:
  - `go test ./...`
  - `go build -o .omo/evidence/obsidian-sync-agent ./cmd/obsidian-sync-agent`
  - `GOOS=darwin GOARCH=arm64 go build -o .omo/evidence/builds/obsidian-sync-agent-darwin-arm64 ./cmd/obsidian-sync-agent`
  - `GOOS=darwin GOARCH=amd64 go build -o .omo/evidence/builds/obsidian-sync-agent-darwin-amd64 ./cmd/obsidian-sync-agent`
  - `GOOS=linux GOARCH=amd64 go build -o .omo/evidence/builds/obsidian-sync-agent-linux-amd64 ./cmd/obsidian-sync-agent`
  - `GOOS=linux GOARCH=arm64 go build -o .omo/evidence/builds/obsidian-sync-agent-linux-arm64 ./cmd/obsidian-sync-agent`
  - `GOOS=windows GOARCH=amd64 go build -o .omo/evidence/builds/obsidian-sync-agent-windows-amd64.exe ./cmd/obsidian-sync-agent`
- Real-surface CLI QA:
  - happy path: run the built binary against a live local FastAPI test server and sync two temporary vaults through create/update/delete/conflict flows; evidence transcript at `.omo/evidence/task-8-go-sync-cli-migration-live-sync.txt`.
  - failure path: run the built binary with missing `--server`/env/config and assert exit code `2` plus configuration error; evidence transcript at `.omo/evidence/task-8-go-sync-cli-migration-config-error.txt`.
- Evidence directory: `.omo/evidence/`.

## Execution strategy
### Parallel execution waves
- Wave 1: Pin current contract and add Go module structure. Tasks 1 and 2 can run together after reading the same references.
- Wave 2: Implement isolated Go packages for config, rules/scanner, manifest/atomic IO, client DTOs. Tasks 3, 4, 5, and 6 can run mostly in parallel after Task 2.
- Wave 3: Implement engine/conflict/Obsidian behavior and CLI command wiring. Tasks 7 and 8 depend on Wave 2.
- Wave 4: Add docs, release builds, and final verification. Tasks 9 and final checks depend on the binary behavior.

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 1 | none | 3, 4, 5, 6, 7, 8, 9 | 2 |
| 2 | none | 3, 4, 5, 6, 7, 8, 9 | 1 |
| 3 | 1, 2 | 7, 8 | 4, 5, 6 |
| 4 | 1, 2 | 7, 8 | 3, 5, 6 |
| 5 | 1, 2 | 7, 8 | 3, 4, 6 |
| 6 | 1, 2 | 7, 8 | 3, 4, 5 |
| 7 | 3, 4, 5, 6 | 8, 9 | none |
| 8 | 7 | 9 | none |
| 9 | 8 | final verification | none |

## Todos
> Implementation + Test = ONE todo. Never separate.
<!-- APPEND TASK BATCHES BELOW THIS LINE WITH edit/apply_patch - never rewrite the headers above. -->

- [x] 1. Pin the current Python sync-agent contract before porting.
  What to do / Must NOT do: Add characterization artifacts/tests that capture the current Python CLI help, config precedence, manifest shape, ignore rules, conflict naming/content, dry-run behavior, status behavior, and exit codes. Do not change product behavior in this task.
  Parallelization: Wave 1 | Blocked by: none | Blocks: 3, 4, 5, 6, 7, 8, 9
  References (executor has NO interview context - be exhaustive): `src/obsidian_sync/sync_agent/cli.py:20-114`; `src/obsidian_sync/sync_agent/config.py:8-184`; `src/obsidian_sync/sync_agent/manifest.py:1-93`; `src/obsidian_sync/sync_agent/ignore.py:1-31`; `src/obsidian_sync/sync_agent/conflict.py`; `docs/sync-agent.md:1-154`; `README.md:316-460`; `tests/sync_agent/test_config.py:18-104`; `tests/sync_agent/test_manifest.py:12-50`; `tests/sync_agent/test_ignore.py:6-40`.
  Acceptance criteria (agent-executable): `uv run pytest tests/sync_agent tests/test_sync_rules.py` exits 0; `uv run obsidian-sync-agent --help` and `uv run obsidian-sync-agent sync --help` outputs are saved to `.omo/evidence/task-1-go-sync-cli-migration-help.txt`; no source files outside tests/evidence/docs are modified.
  QA scenarios (name the exact tool + invocation): happy: `uv run obsidian-sync-agent sync --help > .omo/evidence/task-1-go-sync-cli-migration-help.txt` and PASS if output contains `--dry-run`, `--require-obsidian-refresh`, `--vault-root`, `--server`; failure: `env -i uv run obsidian-sync-agent sync --vault-root /tmp/nonexistent` and PASS if exit code is `2` and stderr contains `configuration error`, evidence `.omo/evidence/task-1-go-sync-cli-migration-config-failure.txt`.
  Commit: Y | `test(sync-agent): pin python cli compatibility contract`

- [x] 2. Add the Go module and folder skeleton without behavior.
  What to do / Must NOT do: Add root `go.mod`; create `cmd/obsidian-sync-agent/main.go`; create package directories under `internal/syncagent/` with minimal compilable package files and package-level docs where helpful. Do not add third-party dependencies unless a concrete need is proven.
  Parallelization: Wave 1 | Blocked by: none | Blocks: 3, 4, 5, 6, 7, 8, 9
  References (executor has NO interview context - be exhaustive): `pyproject.toml:18-20` for existing command names; `src/obsidian_sync/sync_agent/__init__.py:1` for client identity; Go module layout guidance; current repo root structure.
  Acceptance criteria (agent-executable): `go test ./...` exits 0; `go build -o .omo/evidence/obsidian-sync-agent ./cmd/obsidian-sync-agent` exits 0; `.omo/evidence/obsidian-sync-agent --help` exits 0 and identifies `obsidian-sync-agent`.
  QA scenarios (name the exact tool + invocation): happy: `.omo/evidence/obsidian-sync-agent --help > .omo/evidence/task-2-go-sync-cli-migration-help.txt` and PASS if it prints `obsidian-sync-agent`; failure: `.omo/evidence/obsidian-sync-agent unknown` and PASS if exit code is non-zero and stderr identifies an unknown command, evidence `.omo/evidence/task-2-go-sync-cli-migration-unknown-command.txt`.
  Commit: Y | `build(go): add sync agent module skeleton`

- [ ] 3. Port configuration loading and CLI/env/file precedence.
  What to do / Must NOT do: Implement `internal/syncagent/config` and command argument parsing so CLI > env > config file > default matches Python. Keep token env-only. Keep default Obsidian base URL, device ID sanitization, hostname fallback, and `--require-obsidian-refresh` semantics.
  Parallelization: Wave 2 | Blocked by: 1, 2 | Blocks: 7, 8
  References (executor has NO interview context - be exhaustive): `src/obsidian_sync/sync_agent/config.py:8-184`; `tests/sync_agent/test_config.py:18-104`; `docs/sync-agent.md:32-72`; `README.md:350-397`.
  Acceptance criteria (agent-executable): `go test ./internal/syncagent/config ./cmd/obsidian-sync-agent` exits 0 with tests for file-only, env-over-file, CLI-over-env, token env-only, missing required server/vault errors, device ID sanitization, Obsidian API key env, and require-refresh override.
  QA scenarios (name the exact tool + invocation): happy: create a temp vault config file and run `.omo/evidence/obsidian-sync-agent status --vault-root "$TMP_VAULT" --server http://127.0.0.1:9 --vault-id cli-vault --device-id cli-device`, PASS if config layer selects CLI values before network failure; failure: run `env -i .omo/evidence/obsidian-sync-agent status --vault-root "$TMP_VAULT"` with no config, PASS if exit code is `2` and message names missing server base URL, evidence `.omo/evidence/task-3-go-sync-cli-migration-config.txt`.
  Commit: Y | `feat(sync-agent): port configuration resolution to go`

- [ ] 4. Port manifest, atomic file writes, ignore rules, scanner, and hashing.
  What to do / Must NOT do: Implement `internal/syncagent/manifest`, `atomicfile`, `rules`, and `scanner`. Preserve JSON key names, sorted deterministic save where practical, hidden-directory behavior, Markdown-only v1, conflict file exclusion, SHA-256 hex hashing, and local change classification. Do not scan `.obsidian`, `.obsidian-sync-agent`, `.trash`, hidden files, `.DS_Store`, `Thumbs.db`, or conflict files.
  Parallelization: Wave 2 | Blocked by: 1, 2 | Blocks: 7, 8
  References (executor has NO interview context - be exhaustive): `src/obsidian_sync/sync_agent/manifest.py:1-93`; `src/obsidian_sync/sync_agent/atomic.py`; `src/obsidian_sync/sync_agent/scanner.py:1-66`; `src/obsidian_sync/sync_agent/ignore.py:1-31`; `src/obsidian_sync/domain/sync_rules.py:1-35`; `tests/sync_agent/test_manifest.py:12-50`; `tests/sync_agent/test_scanner.py`; `tests/sync_agent/test_ignore.py`; `tests/test_sync_rules.py`.
  Acceptance criteria (agent-executable): `go test ./internal/syncagent/manifest ./internal/syncagent/atomicfile ./internal/syncagent/rules ./internal/syncagent/scanner` exits 0 and includes parity cases from the Python tests.
  QA scenarios (name the exact tool + invocation): happy: run a Go test fixture creating `notes/a.md`, `notes/image.png`, `.obsidian/config.md`, `.obsidian-sync-agent/config.json`, `.trash/old.md`, `notes/a.conflict.dev.20260707-000000.md`, PASS if only `notes/a.md` is scanned; failure: save manifest then list `{vault_root}/.obsidian-sync-agent`, PASS if no `.tmp-` leftovers remain, evidence `.omo/evidence/task-4-go-sync-cli-migration-scanner-manifest.txt`.
  Commit: Y | `feat(sync-agent): port manifest and vault scanning to go`

- [x] 5. Port the sync API HTTP client and DTOs.
  What to do / Must NOT do: Implement `internal/syncagent/client` with response-envelope parsing, Bearer token header, path segment encoding, timeout, JSON DTOs, `SYNC_CONFLICT` detection, and methods for device registration, changes, status, get file, put file, and delete file with JSON body. Do not change endpoint paths or request/response field names.
  Parallelization: Wave 2 | Blocked by: 1, 2 | Blocks: 7, 8
  References (executor has NO interview context - be exhaustive): `src/obsidian_sync/sync_agent/client.py:18-210`; `src/obsidian_sync/api/routes/sync.py:22-136`; `src/obsidian_sync/schemas/sync.py:1-55`; `tests/test_sync_api.py`; `tests/test_sync_agent_integration.py:16-24`.
  Acceptance criteria (agent-executable): `go test ./internal/syncagent/client` exits 0 with `httptest.Server` cases for success envelope, non-JSON response, error envelope, `SYNC_CONFLICT`, path encoding with spaces/slashes, Authorization header presence/absence, and DELETE JSON body.
  QA scenarios (name the exact tool + invocation): happy: `go test ./internal/syncagent/client -run TestClientPutDeleteAndStatus -v > .omo/evidence/task-5-go-sync-cli-migration-client.txt`, PASS if tests verify method/path/body/header; failure: `go test ./internal/syncagent/client -run TestClientSyncConflict -v`, PASS if conflict details are preserved, evidence same file.
  Commit: Y | `feat(sync-agent): port sync api client to go`

- [ ] 6. Port conflict file generation and optional Obsidian refresh.
  What to do / Must NOT do: Implement `internal/syncagent/conflict` and `internal/syncagent/obsidian`. Preserve conflict filename format, content headings, duplicate prevention by path/device/server revision, server/local deleted placeholders, Obsidian health probe, optional reload command, API key env usage, TLS verify option, and require-refresh outcome. Do not make Obsidian required by default.
  Parallelization: Wave 2 | Blocked by: 1, 2 | Blocks: 7, 8
  References (executor has NO interview context - be exhaustive): `src/obsidian_sync/sync_agent/conflict.py`; `src/obsidian_sync/sync_agent/obsidian.py`; `tests/sync_agent/test_conflict.py`; `docs/sync-agent.md:220-330`; `README.md:466`.
  Acceptance criteria (agent-executable): `go test ./internal/syncagent/conflict ./internal/syncagent/obsidian` exits 0 with tests for filename format, duplicate reuse, deleted placeholders, health probe success/failure, reload command requiring API key, and require-refresh summary behavior.
  QA scenarios (name the exact tool + invocation): happy: `go test ./internal/syncagent/conflict -run TestWriteConflictFile -v > .omo/evidence/task-6-go-sync-cli-migration-conflict.txt`, PASS if file content includes Local Version and Server Version; failure: `go test ./internal/syncagent/obsidian -run TestReloadCommandWithoutAPIKey -v`, PASS if result is not ok and no panic occurs, evidence `.omo/evidence/task-6-go-sync-cli-migration-obsidian.txt`.
  Commit: Y | `feat(sync-agent): port conflict and obsidian refresh behavior`

- [ ] 7. Port sync engine orchestration and status behavior.
  What to do / Must NOT do: Implement `internal/syncagent/engine` to match Python lifecycle: validate/load manifest, register device, pull first, skip same-run push for pull-conflicted paths, scan, push new/modified/deleted with correct base revisions, handle conflict resolution, pull second with `device_id`, save manifest, run Obsidian refresh, and status output. Preserve dry-run as no local writes and no pushes. Do not auto-merge conflicts.
  Parallelization: Wave 3 | Blocked by: 3, 4, 5, 6 | Blocks: 8, 9
  References (executor has NO interview context - be exhaustive): `src/obsidian_sync/sync_agent/engine.py:48-429`; `docs/sync-agent.md:154-246`; `tests/test_sync_agent_integration.py`; `tests/test_sync_concurrency.py`; `docs/sync-api.md:1-420`.
  Acceptance criteria (agent-executable): `go test ./internal/syncagent/engine` exits 0 with fake client tests for dry-run, pull write, pull delete, push create/update/delete, push conflict, resolved conflict, server-delete acceptance, cursor advancement, and no duplicate conflict push in same run.
  QA scenarios (name the exact tool + invocation): happy: `go test ./internal/syncagent/engine -run TestRunSyncCreateUpdateDelete -v > .omo/evidence/task-7-go-sync-cli-migration-engine.txt`, PASS if summary counts match expected pushed/applied/deleted; failure: `go test ./internal/syncagent/engine -run TestRunSyncConflictDoesNotAutoMerge -v`, PASS if conflict file is recorded and original file is not overwritten, evidence same file.
  Commit: Y | `feat(sync-agent): port sync engine to go`

- [ ] 8. Wire the Go CLI binary and run live parity QA against the Python server.
  What to do / Must NOT do: Wire `cmd/obsidian-sync-agent` to config, engine, logging, subcommands, flags, stdout/stderr behavior, and exit codes. Build the binary and drive it against a live local FastAPI test server. Do not remove Python CLI yet.
  Parallelization: Wave 3 | Blocked by: 7 | Blocks: 9
  References (executor has NO interview context - be exhaustive): `src/obsidian_sync/sync_agent/cli.py:1-117`; `tests/test_sync_agent_integration.py:67-263`; `README.md:371-408`; `docs/sync-agent.md:16-31`; `docs/sync-agent.md:340-356`.
  Acceptance criteria (agent-executable): `go test ./...` exits 0; `go build -o .omo/evidence/obsidian-sync-agent ./cmd/obsidian-sync-agent` exits 0; live QA script or pytest fixture syncs two temp vaults through create, update, delete, conflict, resolved conflict, status, and dry-run; missing config exits 2; conflict exits 1; require-refresh failure exits 3.
  QA scenarios (name the exact tool + invocation): happy: start server with `uv run uvicorn obsidian_sync.app:app --host 127.0.0.1 --port <free-port>` using the test database setup from `tests/test_sync_agent_integration.py`, then run `.omo/evidence/obsidian-sync-agent sync --server http://127.0.0.1:<port> --vault-id <test-vault> --vault-root <tmp-vault-a> --device-id deva`; PASS if a note created in vault A appears through server sync in vault B and transcript is saved to `.omo/evidence/task-8-go-sync-cli-migration-live-sync.txt`; failure: run `.omo/evidence/obsidian-sync-agent sync --vault-root <tmp-vault>` with no server/vault config, PASS if exit code is `2`, evidence `.omo/evidence/task-8-go-sync-cli-migration-config-error.txt`.
  Commit: Y | `feat(sync-agent): wire go cli and prove live parity`

- [ ] 9. Update documentation and add cross-platform release/build automation.
  What to do / Must NOT do: Update README/docs to explain installing/downloading the Go binary, local development commands, and the coexistence/deprecation posture for the Python CLI. Add a Makefile or script for local builds and optionally `.goreleaser.yaml` plus GitHub Actions only after manual cross-builds pass. Do not claim release availability until artifacts are actually built.
  Parallelization: Wave 4 | Blocked by: 8 | Blocks: final verification
  References (executor has NO interview context - be exhaustive): `README.md:340-408`; `README.md:761-790`; `docs/sync-agent.md:1-31`; `pyproject.toml:18-20`; root `Dockerfile` for server-only context; Go cross-build commands in this plan.
  Acceptance criteria (agent-executable): docs contain a Go binary install/run section, Python server instructions remain intact, and cross-build commands below exit 0 for all target triples.
  QA scenarios (name the exact tool + invocation): happy: run all cross-build commands listed in Verification strategy and PASS if each artifact exists under `.omo/evidence/builds`; failure: run the Windows binary build command with `.exe` output and PASS if the output file exists and has non-zero size, evidence `.omo/evidence/task-9-go-sync-cli-migration-builds.txt`.
  Commit: Y | `docs(sync-agent): document go binary distribution`

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [ ] F1. Plan compliance audit: inspect `git diff --stat`, `git diff --name-only`, and this plan. PASS only if product-code changes match the staged plan and Python server scope is untouched except docs/tests explicitly named above.
- [ ] F2. Code quality review: run `go test ./...`, `go vet ./...`, `uv run ruff check .`, and `uv run mypy`. PASS only if no new failures are introduced; if local PostgreSQL is missing, record that DB integration tests could not run and run non-DB tests.
- [ ] F3. Real manual QA: run the built Go binary through help, missing config, dry-run, live sync, conflict, resolved conflict, status, and require-refresh-failure scenarios. PASS only with transcripts under `.omo/evidence/`.
- [ ] F4. Scope fidelity: compare Go behavior against `docs/sync-agent.md`, `README.md`, and Python sync-agent tests. PASS only if config/env/manifest/conflict/ignore/exit-code contracts are unchanged or any intentional deviation has explicit user approval.

## Commit strategy
- Use small commits aligned to todos when practical.
- Keep commits bisectable: tests and implementation for one behavior in the same commit.
- Suggested sequence:
  - `test(sync-agent): pin python cli compatibility contract`
  - `build(go): add sync agent module skeleton`
  - `feat(sync-agent): port configuration resolution to go`
  - `feat(sync-agent): port manifest and vault scanning to go`
  - `feat(sync-agent): port sync api client to go`
  - `feat(sync-agent): port conflict and obsidian refresh behavior`
  - `feat(sync-agent): port sync engine to go`
  - `feat(sync-agent): wire go cli and prove live parity`
  - `docs(sync-agent): document go binary distribution`
- Do not squash evidence-producing commits until the final verification wave is complete.

## Success criteria
- A root Go module builds a standalone `obsidian-sync-agent` binary.
- The Python FastAPI server remains the server implementation.
- The Go client supports the existing `sync` and `status` commands, flags, env vars, config file, manifest file, conflict handling, ignore rules, dry-run behavior, status behavior, and exit codes.
- The Go binary passes package tests, live CLI parity QA, and cross-platform build checks for macOS, Linux, and Windows targets.
- Documentation tells users how to use the Go binary without requiring Python/uv for the client.
- The Python sync-agent remains available until parity is proven and a later explicit removal/deprecation step is approved.
