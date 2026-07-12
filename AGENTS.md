# Repository Guidelines

## Project Structure & Module Organization

This is a Python 3.14 FastAPI service using a `src/` layout. Application code lives in `src/obsidian_sync/`: `api/` contains routers and dependencies, `core/` holds configuration and shared error handling, `db/` contains SQLAlchemy setup and models, `domain/` contains pure domain helpers, `repositories/` handles persistence, `services/` coordinates workflows, and `schemas/` defines API payloads. CLI and utility entry points are in `src/obsidian_sync/cli.py`, `main.py`, and `scripts/`. Database migrations live in `alembic/versions/`; raw DDL is in `db/`; design notes are in `docs/`.

## Build, Test, and Development Commands

- `uv sync --dev`: install runtime and development dependencies from `uv.lock`.
- `uv run uvicorn obsidian_sync.app:app --host 0.0.0.0 --port 8000`: run the API locally.
- `uv run obsidian-sync`: run the packaged CLI entry point.
- `uv run ruff check .`: lint imports and Python code.
- `uv run ruff format .`: format code using the repository Ruff settings.
- `uv run mypy`: run strict type checking for `src` and `main.py`.
- `docker build -t obsidian-sync-api .`: build the API container image.

## Coding Style & Naming Conventions

Use four-space indentation, single quotes, and an 88-character line length; these are enforced by Ruff. Prefer absolute imports because relative imports are banned. Keep domain logic in `domain/`, orchestration in `services/`, persistence in `repositories/`, and HTTP-specific behavior in `api/`. Use snake_case for modules, functions, and variables; use PascalCase for classes and Pydantic/SQLAlchemy models.

## Testing Guidelines

The project uses pytest with `testpaths = ["tests"]`. Name new test files `test_<module>.py` and place focused unit tests under the matching area, such as `tests/sync_agent/` for local agent behavior and top-level `tests/test_<feature>.py` for API/service flows. Before submitting changes, run the full verification gate:

```
uv run ruff check . && uv run mypy && uv run pytest -q
```

API and integration tests need local PostgreSQL with the pgvector extension; they create and migrate a dedicated `obsidian_sync_test` database. Ollama is stubbed in tests, so no other external service is required.

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
