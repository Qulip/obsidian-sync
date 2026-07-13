# PYTHON SERVICE KNOWLEDGE BASE

## OVERVIEW

FastAPI service owning REST, REST-shaped MCP, and streamable MCP adapters over
the same PostgreSQL-backed vault, search, and indexing workflows.

## STRUCTURE

```text
api/            # HTTP dependencies and adapters
core/           # settings, auth, errors, response envelope
db/             # async SQLAlchemy setup, models, pgvector type
domain/         # pure validation, chunking, path, and sync/search rules
repositories/   # async persistence operations
services/       # revision sync, vault, indexing, and search workflows
schemas/        # Pydantic API contracts
sync_agent/     # Python compatibility client; see its local AGENTS.md
```

## WHERE TO LOOK

| Task | Location | Notes |
|---|---|---|
| App/lifespan | `app.py` | Builds import-time ASGI application and co-hosts MCP. |
| Auth/dependencies | `api/deps.py`, `core/` | Routes must use dependency aliases. |
| Vault mutation | `services/revision_sync.py` | Canonical revision, conflict, and staged-storage path. |
| Search behavior | `services/search.py` | Hybrid RRF, rerank fallback, feedback logging. |
| Persistence | `repositories/`, `db/` | Repositories do not commit. |

## CONVENTIONS

- Keep routes thin: inject `Settings` and `AsyncSession`, construct a service,
  and return `ok(...)` with `ResponseEnvelope[T]`.
- Raise `AppError` for expected failures. Global handlers own error envelopes.
- Keep transaction ownership in `get_db_session`; do not create engines or
  commit sessions in routes or repositories.
- Put pure logic in `domain/`, workflows in `services/`, and queries in
  `repositories/`. Use absolute imports and the strict Python style in root.
- Use Alembic for schema changes; keep ORM models and migration history aligned.

## CRITICAL INVARIANTS

- Route write/delete/restore through `RevisionSyncService`. Lock the file row
  before base-revision checks and use staged filesystem replacement with rollback.
- `base_revision` is a file revision, not the vault cursor. An identical retry
  is a no-op; stale writes record a conflict and return 409.
- Hybrid search returns RRF-selected results while retaining cosine `score`;
  preserve `matched_by` and apply `min_score` only to vector-only results.

## COMMANDS

```bash
uv run ruff check . && uv run mypy && uv run pytest -q
uv run uvicorn obsidian_sync.app:app --host 0.0.0.0 --port 8000
```

The pytest fixture recreates `obsidian_sync_test` and migrates it to head.
