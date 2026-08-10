#!/bin/sh
# Applies the Alembic migration chain before starting the ASGI process so a
# fresh deployment cannot serve requests against an unmigrated schema.
# Set OBSIDIAN_SYNC_RUN_MIGRATIONS=0 to skip (e.g. when a separate release job
# owns migrations).
set -eu

run_migrations() {
    attempt=1
    max_attempts="${OBSIDIAN_SYNC_MIGRATION_ATTEMPTS:-10}"
    delay="${OBSIDIAN_SYNC_MIGRATION_RETRY_SECONDS:-3}"

    while :; do
        echo "[entrypoint] alembic upgrade head (attempt ${attempt}/${max_attempts})"
        if uv run --frozen alembic upgrade head; then
            echo '[entrypoint] migrations applied'
            return 0
        fi
        if [ "${attempt}" -ge "${max_attempts}" ]; then
            echo '[entrypoint] migrations failed; refusing to start' >&2
            return 1
        fi
        attempt=$((attempt + 1))
        sleep "${delay}"
    done
}

if [ "${OBSIDIAN_SYNC_RUN_MIGRATIONS:-1}" = '1' ]; then
    run_migrations
else
    echo '[entrypoint] OBSIDIAN_SYNC_RUN_MIGRATIONS=0, skipping migrations'
fi

exec "$@"
