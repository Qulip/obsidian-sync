# PROJECT KNOWLEDGE BASE

## OVERVIEW

Obsidian Sync is a Python 3.14 FastAPI service and a Go 1.23 local sync-agent
distribution. The Python sync-agent CLI remains a compatibility and rollback path.

## STRUCTURE

```text
src/obsidian_sync/     # FastAPI service and Python compatibility agent
cmd/                   # Go agent command entry point
internal/syncagent/    # Go agent implementation
alembic/               # authoritative schema migration chain
db/                    # baseline DDL reference only; never a migration substitute
tests/                 # Python unit, API, and integration tests
docs/                  # current protocol and operational contracts
scripts/               # operational helper scripts (backup, cleanup, eval)
SKILLS/                # agent skills packaged into releases
install.sh, install.ps1  # end-user agent installers
Makefile               # Go agent build targets
```

### Nested knowledge bases

Read the AGENTS.md nearest to the code you are changing; each one owns the
conventions and invariants for its subtree.

| File | Scope |
|---|---|
| `src/obsidian_sync/AGENTS.md` | FastAPI service layering, route/session rules, critical invariants |
| `src/obsidian_sync/sync_agent/AGENTS.md` | Python compatibility agent CLI |
| `internal/syncagent/AGENTS.md` | Go agent protocol and local filesystem safety |
| `alembic/AGENTS.md` | Migration chain conventions |

## WHERE TO LOOK

| Task | Location | Notes |
|---|---|---|
| ASGI startup and MCP mounting | `src/obsidian_sync/app.py` | `create_app()` builds the import-time app and owns the post-sync index worker lifespan. |
| API request handling | `src/obsidian_sync/api/` | REST and REST-shaped MCP adapters. |
| Sync write semantics | `src/obsidian_sync/services/revision_sync.py` | Revision, storage, conflict, and rollback workflow. |
| Post-sync indexing | `src/obsidian_sync/services/post_sync_indexing.py` | Dispatcher and background worker that schedule indexing after a committed sync. |
| Production local client | `internal/syncagent/` | Go implementation built by `make build-agent`. |
| Agent self-update | `internal/syncagent/updater/` | Release lookup and binary replacement for `obsisync`. |
| Schema evolution | `alembic/versions/` | Linear migration history; raw DDL is reference only. |
| Container startup | `Dockerfile`, `docker-entrypoint.sh` | Entrypoint applies migrations before the ASGI process starts. |

## CODE MAP

| Symbol | Type | Location | Role |
|---|---|---|---|
| `create_app` | function | `app.py` | ASGI lifespan, error handling, router/MCP mounting |
| `RevisionSyncService` | class | `services/revision_sync.py` | Canonical revisioned vault writes |
| `VaultSyncService` | class | `services/vault_sync.py` | MCP-oriented vault workflows |
| `PostSyncIndexDispatcher` | protocol | `services/post_sync_indexing.py` | Schedule contract for indexing paths touched by a committed sync |
| `AsyncPostSyncIndexWorker` | class | `services/post_sync_indexing.py` | Background dispatcher implementation started by the app lifespan |

## Project Structure & Module Organization

This is a Python 3.14 FastAPI service using a `src/` layout. Application code lives in `src/obsidian_sync/`: `api/` contains routers and dependencies, `core/` holds configuration and shared error handling, `db/` contains SQLAlchemy setup and models, `domain/` contains pure domain helpers, `repositories/` handles persistence, `services/` coordinates workflows, and `schemas/` defines API payloads. `obsidian_sync.app:app` is the ASGI entry point; `main.py` is only a CLI compatibility launcher. The Go agent lives in `cmd/obsidian-sync-agent` and `internal/syncagent`; it is the default distribution as `obsisync`, while `src/obsidian_sync/sync_agent` is retained as the `obsidian-sync-agent` compatibility CLI. Both the Go (`obsisync watch`, via `internal/syncagent/watch`, backed by `fsnotify`) and Python (`obsidian-sync-agent watch`, backed by `watchdog`) CLIs support `watch`. Database migrations live in `alembic/versions/`; raw DDL is in `db/`; design notes are in `docs/`.

## Build, Test, and Development Commands

- `uv sync --dev`: install runtime and development dependencies from `uv.lock`.
- `uv run uvicorn obsidian_sync.app:app --host 0.0.0.0 --port 8000`: run the API locally.
- `uv run obsidian-sync`: run the packaged CLI entry point.
- `uv run ruff check .`: lint imports and Python code.
- `uv run ruff format .`: format code using the repository Ruff settings.
- `uv run mypy`: run strict type checking for `src` and `main.py`.
- `docker build -t obsidian-sync-api .`: build the API container image.
- `go test ./... && go vet ./...`: verify the Go sync agent.
- `make build-agent`: build the Go agent at `dist/obsisync/`.
- `make build-agent-all VERSION=<tag>`: cross-compile the release artifacts.

## Deployment

The container entrypoint (`docker-entrypoint.sh`) runs `alembic upgrade head`
with bounded retries before `exec`ing the ASGI command, so a fresh deployment
never serves requests against an unmigrated schema. Set
`OBSIDIAN_SYNC_RUN_MIGRATIONS=0` when a separate release job owns migrations;
`OBSIDIAN_SYNC_MIGRATION_ATTEMPTS` and `OBSIDIAN_SYNC_MIGRATION_RETRY_SECONDS`
tune the retry loop. If migrations never succeed, the entrypoint exits instead
of starting the API.

## Coding Style & Naming Conventions

Use four-space indentation, single quotes, and an 88-character line length; these are enforced by Ruff. Prefer absolute imports because relative imports are banned. Keep domain logic in `domain/`, orchestration in `services/`, persistence in `repositories/`, and HTTP-specific behavior in `api/`. Use snake_case for modules, functions, and variables; use PascalCase for classes and Pydantic/SQLAlchemy models.

Routes use dependency aliases, `ok(...)` response envelopes, and `AppError` for expected failures. Repositories execute persistence operations but never commit; the session dependency owns commit and rollback. Schema changes require a new Alembic revision. Keep the Python and Go agent protocol aligned unless a deliberately coordinated contract change says otherwise.

## Testing Guidelines

The project uses pytest with `testpaths = ["tests"]`. Name new test files `test_<module>.py` and place focused unit tests under the matching area, such as `tests/sync_agent/` for local agent behavior and top-level `tests/test_<feature>.py` for API/service flows. Before submitting changes, run the full verification gate:

```
uv run ruff check . && uv run mypy && uv run pytest -q
```

API and integration tests need local PostgreSQL with the pgvector extension; they create and migrate a dedicated `obsidian_sync_test` database. Ollama is stubbed in tests, so no other external service is required.

Go tests are colocated as `*_test.go`. CI runs the Python gate and the Go gate as separate jobs on every push and pull request, so both are verified regardless of what a change touches. Locally, run the gate matching your change: the Python gate above, plus `go vet ./... && go test ./...` for changes under `cmd/` or `internal/`.

### Local Test Database

Put the local database URL in `.env` (gitignored; see `.env.example`) as `OBSIDIAN_POSTGRESQL_URL=...`. `tests/conftest.py` reads it automatically — never inline credentials in shell commands. If no local PostgreSQL is running, start a disposable one:

```
docker run -d --rm --name obsidian-sync-test-pg -p 5432:5432 \
  -e POSTGRES_PASSWORD=testpw pgvector/pgvector:pg17
```

## Commit & Pull Request Guidelines

The current history uses concise scoped messages such as `init: 초기 구현`. Keep commits short and imperative, preferably `scope: summary` (`api: add vault lookup`, `db: add migration`). Pull requests should include a brief purpose statement, key implementation notes, verification commands run, linked issues if applicable, and sample requests or responses for API changes.

## Agent Workflow

- Delegate implementation, analysis, and test-writing work to subagents using `model: sonnet`; the main agent orchestrates, reviews results, and makes judgment calls.
- Run the verification gate (`uv run ruff check . && uv run mypy && uv run pytest -q`) before every commit and before reporting any task as complete. Report actual command output, not expectations.
- For multi-phase work (e.g. P1/P2 improvement plans), checkpoint progress after each phase — commit completed phases individually and record remaining-phase status in the relevant `docs/` file — so an interrupted session can resume with full context.

## Security & Configuration Tips

Do not commit secrets. Production requires `OBSIDIAN_SYNC_DATABASE_URL` and `KNOWLEDGE_API_TOKEN`; set them through the environment. Keep generated vault backups, archives, and local `.venv` contents out of version control.

## ANTI-PATTERNS (THIS PROJECT)

- Do not bypass `RevisionSyncService` for vault mutations: every real write,
  delete, or restore needs one revision, an event, staged storage, and rollback.
- Do not compare `base_revision` to the vault cursor, add automatic conflict
  merging, or sync ignored/conflict paths.
- Do not modify applied Alembic revisions or use raw DDL instead of a migration.
