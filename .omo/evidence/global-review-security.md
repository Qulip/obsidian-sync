# Global Review Lane 4 - Security Review

Status: FAIL
Recommendation: REQUEST_CHANGES
Confidence: high

Scope reviewed:
- Branch diff against `main`.
- `cmd/obsidian-sync-agent`.
- `internal/syncagent/*`.
- CLI config, token handling, HTTP clients, TLS verify behavior, local file writes, conflict files, path handling, Obsidian Local REST API key usage, build artifacts/docs.
- Docs/evidence secret scan was performed filename-only/redacted; no unredacted real secret was identified, but placeholder/test tokens are present.

Skill perspective check:
- `omo:remove-ai-slops` was loaded from `/Users/ia03060_mac/.codex/plugins/cache/sisyphuslabs/omo/4.16.0/skills/remove-ai-slops/SKILL.md`; used as a read-only slop/overfit lens. No deletion-only or tautological test blocker was found in the reviewed Go unit tests.
- `omo:programming` and `references/go/README.md` were loaded; used as a Go boundary/type/tooling lens. The diff violates the programming security perspective through lexical-only path containment around filesystem trust boundaries and permissive TLS-disable behavior.
- `security-review` was loaded from `/Users/ia03060_mac/.codex/skills/ecc/security-review/SKILL.md`.
- `omo:git-master` was loaded and used in STATUS mode only.

Commands run:
- `git status --short && git branch --show-current && git diff --name-only main...HEAD` -> exit 0.
- `git diff --stat main...HEAD` -> exit 0.
- `git diff --name-status main...HEAD -- cmd/obsidian-sync-agent internal/syncagent docs/sync-agent.md README.md Makefile go.mod tests/sync_agent/test_contract.py` -> exit 0.
- `go test -count=1 ./...` -> exit 0.
- `go vet ./...` -> exit 0.
- `git diff --check main...HEAD` -> exit 2. Failures are whitespace/newline issues in tracked `.omo/evidence/*` files, not a product-code security issue.
- Redacted/filename-only secret pattern scan over `.omo`, `docs`, `README.md`, `Makefile`, `cmd`, `internal`, `tests` -> exit 0 with candidate placeholders/test references only after redacted inspection.
- Changed Go pure LOC >250 check -> exit 0, no files reported.
- `git ls-files .omo/evidence/builds .omo/evidence/obsidian-sync-agent` -> exit 0, six executable binaries are tracked.

## CRITICAL

None found.

## HIGH

1. Vault-root containment can be bypassed through symlink components.

Evidence:
- `internal/syncagent/engine/util.go:14` only performs lexical validation with `filepath.Clean`, `filepath.IsAbs`, and `filepath.Join`. It does not reject symlinks or verify the resolved destination remains under the vault root.
- `internal/syncagent/engine/pull.go:141` writes server content to the destination returned by `vaultPath`.
- `internal/syncagent/atomicfile/atomicfile.go:8` creates the parent directory, creates a temp file in that directory, and renames it to the destination. If any parent component inside the vault is a symlink to an outside directory, the write occurs outside the vault.
- `internal/syncagent/conflict/conflict.go:61` and `internal/syncagent/conflict/conflict.go:163` have the same symlinked-parent exposure for conflict files.
- `internal/syncagent/scanner/scanner.go:34` walks the vault and `internal/syncagent/scanner/scanner.go:97` opens syncable files by path. A symlinked `.md` file inside the vault can cause the agent to hash/read/upload a file outside the vault.

Impact:
- A compromised or malicious sync server can send a path such as `linked-dir/file.md` where `linked-dir` is a symlink inside the vault to an outside directory, causing local writes outside the vault.
- A local symlinked `.md` file can leak outside-vault file contents to the sync API.

Blocker:
- Reject symlinked files/directories for sync, or resolve every existing path component with `EvalSymlinks`/`Lstat` and enforce the resolved absolute path remains inside the resolved vault root before every read, write, delete, and conflict write. Add tests for symlinked file reads and symlinked parent directory writes.

2. Executable build artifacts are tracked and docs install from `.omo/evidence`.

Evidence:
- `git ls-files` reports tracked executable blobs:
  - `.omo/evidence/builds/obsidian-sync-agent-darwin-amd64`
  - `.omo/evidence/builds/obsidian-sync-agent-darwin-arm64`
  - `.omo/evidence/builds/obsidian-sync-agent-linux-amd64`
  - `.omo/evidence/builds/obsidian-sync-agent-linux-arm64`
  - `.omo/evidence/builds/obsidian-sync-agent-windows-amd64.exe`
  - `.omo/evidence/obsidian-sync-agent`
- `Makefile:4` sets `BUILD_DIR := .omo/evidence/builds`.
- `README.md:348` through `README.md:351` instructs building and installing from `.omo/evidence/builds/obsidian-sync-agent`.
- `docs/sync-agent.md:16` through `docs/sync-agent.md:19` gives the same install path.

Impact:
- Binary blobs are not reviewable in the branch diff and can diverge from source. Normalizing `.omo/evidence` as an install source creates release/provenance ambiguity and supply-chain risk.

Blocker:
- Remove tracked executable artifacts from the branch, ignore build outputs, and document a source build path or release artifact path with checksums/provenance outside `.omo/evidence`.

## MEDIUM

1. Obsidian TLS verification defaults to disabled and is not constrained to loopback.

Evidence:
- `internal/syncagent/config/doc.go:16` defaults Obsidian to `https://127.0.0.1:27124`, but `VerifyTLS` defaults false.
- `internal/syncagent/config/config_test.go:248` asserts default `Obsidian.VerifyTLS` is false.
- `internal/syncagent/obsidian/obsidian.go:98` through `internal/syncagent/obsidian/obsidian.go:102` sets `tls.Config{InsecureSkipVerify: true}` whenever `VerifyTLS` is false.
- `internal/syncagent/obsidian/obsidian.go:179` through `internal/syncagent/obsidian/obsidian.go:180` sends the Obsidian API key as a bearer token.

Risk:
- If `obsidian.base_url` is changed to a non-loopback HTTPS endpoint while `verify_tls` remains false, the Local REST API key can be sent to an unauthenticated endpoint under MITM.

Recommended change:
- Permit `verify_tls: false` only for loopback hosts, or default to verification on non-loopback hosts and document an explicit unsafe-local mode.

2. Obsidian Local REST API key is accepted from config file and docs show inline config-key usage.

Evidence:
- `internal/syncagent/config/doc.go:78` through `internal/syncagent/config/doc.go:83` includes `api_key` in `fileObsidianConfig`.
- `internal/syncagent/config/doc.go:202` through `internal/syncagent/config/doc.go:214` accepts the file key and only lets `OBSIDIAN_LOCAL_REST_API_KEY` override it.
- `docs/sync-agent.md:329` through `docs/sync-agent.md:335` shows an inline `"api_key": "your-api-key"` config example.

Risk:
- The main sync API token is env-only, but the Obsidian key can be stored at rest in a vault-local config file with no permission checks. This is easy to copy, back up, or expose accidentally.

Recommended change:
- Prefer env-only Obsidian key handling, or enforce/validate restrictive config-file permissions and remove inline key examples from docs.

## LOW

1. Sync server URL permits plaintext HTTP token transport with documentation-only guardrails.

Evidence:
- `internal/syncagent/client/client.go:24` through `internal/syncagent/client/client.go:39` accepts any URL with scheme and host.
- `internal/syncagent/client/client.go:172` through `internal/syncagent/client/client.go:173` sends `Authorization: Bearer <token>` when configured.
- `README.md:334` through `README.md:336` warns that external access needs HTTPS reverse proxy or private networking.

Risk:
- Users can still configure a non-local `http://` server and send bearer tokens in cleartext. The README warning reduces risk but does not enforce it.

Recommended change:
- Warn or fail when `OBSIDIAN_SYNC_AGENT_SERVER` uses `http://` with a non-loopback host.

2. `git diff --check main...HEAD` fails in tracked evidence files.

Evidence:
- Exit 2, with whitespace/newline errors only in `.omo/evidence/*` files.

Risk:
- Not a product-code security bug, but it reduces release hygiene and makes automated diff checks noisy.

## Positive Security Observations

- Main sync API token is env-only in the Go config path: `internal/syncagent/config/doc.go:121`, with tests at `internal/syncagent/config/config_test.go:56` through `internal/syncagent/config/config_test.go:123`.
- Sync API client sets Authorization only when a token is present: `internal/syncagent/client/client.go:170` through `internal/syncagent/client/client.go:174`.
- Server paths containing absolute paths or lexical `..` traversal are rejected before normal pull writes: `internal/syncagent/engine/util.go:14` through `internal/syncagent/engine/util.go:26`.
- Conflict filenames reject lexical vault escapes: `internal/syncagent/conflict/conflict.go:71` through `internal/syncagent/conflict/conflict.go:87`.
- `go test -count=1 ./...` and `go vet ./...` both passed.

## Blockers

- Fix symlink-aware vault containment before reads, writes, deletes, and conflict writes.
- Remove tracked executable build artifacts from the branch and stop documenting `.omo/evidence` as an install source.

