## Obsidian Sync

FastAPI service for syncing Obsidian Markdown files into vault storage and
indexing them for search.

### Run API Locally

```bash
uv run uvicorn obsidian_sync.app:app --host 0.0.0.0 --port 8000
```

Required production settings:

```bash
export OBSIDIAN_SYNC_DATABASE_URL="postgresql+asyncpg://user:pass@host:5432/db"
export KNOWLEDGE_API_TOKEN="replace-me"
```

### Upload A Vault

```bash
uv run python scripts/upload_obsidian.py \
  --vault-id personal-main \
  --vault-path "/Users/me/ObsidianVault" \
  --server-url "http://mini-pc:8000" \
  --token "$KNOWLEDGE_API_TOKEN"
```

The uploader skips `.obsidian/` and `.trash/` by default. Add optional excludes
with repeated `--exclude-dir` flags, for example `--exclude-dir Archive`.

### Back Up Vault Storage

```bash
uv run python scripts/backup_vaults.py \
  --vault-root vaults \
  --archive-root archives \
  --backup-dir backups
```

Use `--vault-id personal-main` to back up one vault.

### Build API Image

```bash
docker build -t obsidian-sync-api .
docker run --rm -p 8000:8000 \
  -e OBSIDIAN_SYNC_DATABASE_URL="$OBSIDIAN_SYNC_DATABASE_URL" \
  -e KNOWLEDGE_API_TOKEN="$KNOWLEDGE_API_TOKEN" \
  obsidian-sync-api
```
