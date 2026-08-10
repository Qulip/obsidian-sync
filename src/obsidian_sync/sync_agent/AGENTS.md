# PYTHON SYNC-AGENT COMPATIBILITY KNOWLEDGE BASE

## OVERVIEW

Compatibility and rollback client. `uv run obsidian-sync-agent` selects this
implementation; the Go agent (`obsisync`) is the default distribution. Both
agents support `watch` — this one via `watchdog`, the Go one via `fsnotify`.

## WHERE TO LOOK

| Task | Location | Notes |
|---|---|---|
| CLI modes | `cli.py` | `sync`, `status`, and `watch`. |
| Sync orchestration | `engine.py` | Pull, scan/local diff, push, final pull. |
| Server protocol | `client.py` | HTTP requests, revisions, and conflicts. |
| Local persistence | `manifest.py`, `atomic.py` | Persist state only through atomic updates. |
| Vault filtering | `scanner.py`, `ignore.py`, `obsidian.py` | Obsidian and safety exclusions. |
| Conflict handling | `conflict.py`, `resolution.py` | Preserve server conflict semantics. |

## CONVENTIONS

- Keep this protocol-compatible with `internal/syncagent/`; do not silently
  change one agent's behavior without the other.
- Ignore hidden paths, `.obsidian`, `.obsidian-sync-agent`, `.trash`, and
  conflict/backup patterns for scan, push, pull, and watch.
- Markdown always syncs. Attachments need `sync_attachments=True`; server file
  policy and limits are authoritative.
- A server 409 is a deliberate conflict result. Do not add automatic merging.
- Use atomic writes and preserve local divergent content as conflict files.

## COMMAND

```bash
uv run pytest -q tests/sync_agent
```

`tests/conftest.py` requires a configured database URL even for this focused run.
