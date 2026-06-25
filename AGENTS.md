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

No test suite is currently present. When adding tests, create a `tests/` directory that mirrors `src/obsidian_sync/`, name files `test_<module>.py`, and focus on domain helpers, service behavior, and API route contracts. Until a test runner is added to `pyproject.toml`, run `uv run ruff check .` and `uv run mypy` before submitting changes.

## Commit & Pull Request Guidelines

The current history uses concise scoped messages such as `init: 초기 구현`. Keep commits short and imperative, preferably `scope: summary` (`api: add vault lookup`, `db: add migration`). Pull requests should include a brief purpose statement, key implementation notes, verification commands run, linked issues if applicable, and sample requests or responses for API changes.

## Security & Configuration Tips

Do not commit secrets. Production requires `OBSIDIAN_SYNC_DATABASE_URL` and `KNOWLEDGE_API_TOKEN`; set them through the environment. Keep generated vault backups, archives, and local `.venv` contents out of version control.
