# GO SYNC-AGENT KNOWLEDGE BASE

## OVERVIEW

Production local sync-agent implementation. `cmd/obsidian-sync-agent` is the
thin CLI; this subtree owns protocol behavior and safe local vault operations.

## STRUCTURE

```text
engine/       # pull, scan, push, status orchestration
client/       # HTTP contract and typed errors
scanner/      # vault discovery and ignore application
manifest/     # durable local sync state
conflict/     # server-conflict representation
atomicfile/   # atomic local writes
vaultfs/      # containment and filesystem safety
config/       # agent configuration
obsidian/     # Obsidian-specific behavior
```

## CONVENTIONS

- Preserve the server and Python-agent protocol. This Go implementation is the
  default distribution; Python remains a compatibility/rollback path.
- Keep pull → scan/local diff → push → pull orchestration and durable manifest
  updates. A conflict is explicit, never an automatic merge.
- Before applying inbound changes, validate rule/extension eligibility, resolve
  real containment including symlinked parents, verify server content hashes,
  and use atomic writes.
- Preserve local divergent edits as conflict files. Never scan or mutate hidden,
  Obsidian metadata, trash, conflict, or backup paths.

## COMMANDS

```bash
go test ./...
go vet ./...
make build-agent
```

`make build-agent-all` creates generated cross-platform artifacts in `dist/`.
