---
slug: go-sync-cli-migration
status: approved
intent: clear
pending-action: write .omo/plans/go-sync-cli-migration.md
approach: Preserve the Python FastAPI service and migrate only the local sync-agent CLI contract to a Go binary.
review-required: false
---

# Draft: go-sync-cli-migration

## Components (topology ledger)
| id | outcome (one line) | status | evidence path |
| --- | --- | --- | --- |
| C1 | Current Python CLI/API contract understood | active | `pyproject.toml:18-20`, `src/obsidian_sync/sync_agent/cli.py:20-114`, `docs/sync-agent.md:1-154` |
| C2 | Go repository layout selected | active | Go module layout guidance; current repo has one distributable client command |
| C3 | Compatibility/regression strategy selected | active | `tests/sync_agent/test_config.py:18-104`, `tests/sync_agent/test_manifest.py:12-50`, `tests/test_sync_agent_integration.py:247-263` |
| C4 | Cross-platform build/distribution selected | active | Go `GOOS`/`GOARCH` cross-build flow; GoReleaser optional after manual builds pass |

## Open assumptions (announced defaults)
| assumption | adopted default | rationale | reversible? |
| --- | --- | --- | --- |
| Go module placement | Root `go.mod` | One Go command in one repo; avoids nested module overhead and keeps build/release commands simple. | Yes |
| Go CLI dependency | Standard library `flag`-style parsing, no Cobra initially | Existing CLI has two subcommands and a small flag set; avoiding a dependency reduces release surface. | Yes |
| Migration posture | Add Go client alongside Python client first | Existing Python CLI is the compatibility oracle until Go parity is proven; replacing immediately increases user risk. | Yes |
| Release automation | Manual cross-build first, GoReleaser after parity | Reduces moving pieces while behavior is still being ported. | Yes |

## Findings (cited - path:lines)
- `pyproject.toml:18-20` exposes two Python console scripts: `obsidian-sync` for the server and `obsidian-sync-agent` for the local client.
- `src/obsidian_sync/sync_agent/cli.py:20-114` defines the current sync-agent UX: `sync`, `status`, common flags, sync-only flags, logging, and exit codes `0`, `1`, `2`, `3`.
- `src/obsidian_sync/sync_agent/engine.py:48-74` defines the lifecycle: dry-run planning without manifest save, or pull -> scan -> push -> pull -> save manifest -> optional Obsidian refresh.
- `docs/sync-agent.md:1-154` is the compatibility contract: Markdown-only v1, config precedence, manifest format, ignore patterns, and sync order.
- `README.md:316-460` documents installation and operation; current client use requires cloning the Python repo and invoking `uv`, which Go binaries should remove for end users.
- `tests/sync_agent/test_config.py:18-104` pins config precedence and env-only token handling.
- `tests/sync_agent/test_manifest.py:12-50` pins missing manifest behavior, JSON round-trip, and atomic save cleanup.
- `tests/test_sync_agent_integration.py:247-263` drives current CLI behavior against a live server using `uv run obsidian-sync-agent sync`.

## Decisions (with rationale)
- Decision: Keep Python FastAPI service/API in `src/obsidian_sync` unchanged. Rationale: the user asked to migrate the sync CLI for multi-environment client usage, not to rewrite the service.
- Decision: Add Go code under `cmd/obsidian-sync-agent` and `internal/syncagent/*`. Rationale: this is idiomatic for a Go command with private implementation packages and preserves the existing Python `src/` layout.
- Decision: Treat `docs/sync-agent.md` and current Python tests as the compatibility oracle. Rationale: the Go port must be behavior-compatible where users already depend on config, manifest, conflict, and exit-code contracts.
- Decision: Introduce Go binary alongside Python CLI, then deprecate/switch documentation after parity. Rationale: safer rollout and easier rollback.
- Local Metis fallback review: no unresolved contradiction; main risks are scope creep into server rewrite and incomplete compatibility coverage. Both are now represented in Scope OUT, Verification strategy, and Todos.

## Scope IN
- Add a Go implementation plan for `obsidian-sync-agent` as a distributable client binary.
- Specify exact Go folder structure, package responsibilities, tests, live QA, and cross-platform build/release steps.
- Preserve current sync-agent config/env/manifest/conflict/exit-code contracts.
- Include Python regression verification so server/API behavior is not accidentally changed.

## Scope OUT (Must NOT have)
- Must not rewrite the FastAPI server, SQLAlchemy models, repositories, migrations, or MCP server as part of this migration.
- Must not remove the Python `obsidian-sync-agent` entry point until the Go binary passes parity tests and docs are updated in a later explicit step.
- Must not change manifest JSON shape, config file location, env var names, conflict filename/content format, ignore patterns, or exit-code meanings without a separate product decision.
- Must not require users to install Python/uv to run the Go sync client after the release phase.

## Open questions
None blocking. Recommended default remains: introduce the Go client alongside the Python client first.

## Approval gate
status: approved
approved-by: user
approval-message: "승인, plan 작성해줘."
