# DATABASE MIGRATION KNOWLEDGE BASE

## OVERVIEW

Authoritative PostgreSQL `obsidian` schema history. `db/ddl_v1.sql` is a
baseline reference, not a substitute for a migration.

## WHERE TO LOOK

| Task | Location | Notes |
|---|---|---|
| Revision chain | `versions/` | Append after the current linear head. |
| Environment resolution | `env.py`, `alembic.ini` | Database URL aliases and schema setup. |
| ORM alignment | `src/obsidian_sync/db/models.py` | Update models with schema changes. |

## CONVENTIONS

- Add a new, reversible revision; never rewrite an applied revision.
- Maintain complete `upgrade()` and `downgrade()` paths and keep models aligned.
- Generated `tsvector` changes must drop dependent indexes before replacement,
  then recreate schema-qualified indexes in both directions. Expressions need
  immutable SQL functions.
- Preserve compatibility data migrations, including legacy token overwrite
  behavior versus newly created fail-closed tokens.
- The test fixture creates `obsidian_sync_test`, enables pgvector, and migrates
  to head. Its database role needs create/drop privileges.

## COMMANDS

```bash
uv run alembic current
uv run alembic upgrade head
```
