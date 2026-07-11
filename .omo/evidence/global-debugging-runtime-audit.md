# Global Debugging Runtime Audit - Go Sync CLI Migration

Verdict: PASS

Worktree: `/Users/ia03060_mac/workspace/qulip/obsidian-sync-go-cli-worktree`

Runtime: `go version go1.26.5 darwin/arm64`

Blockers: `tmux` is not installed in this environment (`command -v tmux` exit code 1), so CLI scenarios were captured as direct terminal command transcripts with exact invocations and exit codes.

## Hypotheses

1. Root no-subcommand/help behavior regressed after F4 fix.
   - Distinguishing evidence: current rebuilt binary no args must exit 2; root `--help` must exit 0 and print root usage.
   - Result: PASS. No args exited 2 with `the following arguments are required: command`; root `--help` exited 0.

2. Double-dash help rendering diverges from accepted flag parsing or Python contract.
   - Distinguishing evidence: `sync --help` and `status --help` must render `--` long flags; double-dash sync flags must be accepted by the parser and fail later only because the test server is unavailable.
   - Result: PASS. Help renders `--vault-root`, `--vault-id`, `--server`, `--device-id`, `--verbose`, `--dry-run`, and `--require-obsidian-refresh`; double-dash parse probe exited 2 from connection refused, not from `flag provided but not defined`.

3. HTTP timestamp parsing / sync changes response still fails against FastAPI naive timestamps.
   - Distinguishing evidence: targeted timestamp test must run and pass; existing live QA raw FastAPI response must show a naive `created_at`; CLI sync around that response must have completed.
   - Result: PASS. `TestClientGetChanges_readsFastAPINaiveTimestamp` passed. Existing live QA shows `created_at: "2026-07-10T01:20:38.507854"` and successful sync flows.

4. Require-refresh exit 3 or conflict exit 1 is masked by summary/exit handling.
   - Distinguishing evidence: targeted CLI tests must pass for conflict and required-refresh exits; live conflict transcript must show exit 1 after summary; fresh configured require-refresh probe must show summary followed by refresh failure and exit 3.
   - Result: PASS. Targeted CLI exit tests passed. Existing live QA conflict exits 1. Fresh configured require-refresh exited 3 after printing summary and the refresh-required failure.

5. Existing live QA evidence is stale or left background resources running.
   - Distinguishing evidence: current `go build` must succeed; fresh probes must use the rebuilt binary; cleanup scan must show no fake server, uvicorn, CLI, Delve listener, or task Docker container.
   - Result: PASS. Build exit 0; fresh probes used `.omo/evidence/obsidian-sync-agent`; cleanup scan printed no matching processes/containers/listeners.

## manualQa

### surfaceEvidence

| scenario id | criterion reference | surface | exact invocation | verdict | artifactRefs |
|---|---|---|---|---|---|
| S1 | Build required check | Go build CLI | `go build -o .omo/evidence/obsidian-sync-agent ./cmd/obsidian-sync-agent` | PASS, exit 0 | A1 |
| S2 | H1 root no args | Terminal CLI direct shell | `.omo/evidence/obsidian-sync-agent` | PASS, exit 2 | A2 |
| S3 | H1 root help | Terminal CLI direct shell | `.omo/evidence/obsidian-sync-agent --help` | PASS, exit 0 | A2 |
| S4 | H1/H2 sync help | Terminal CLI direct shell | `.omo/evidence/obsidian-sync-agent sync --help` | PASS, exit 0 and double-dash flags rendered | A2 |
| S5 | H1/H2 status help | Terminal CLI direct shell | `.omo/evidence/obsidian-sync-agent status --help` | PASS, exit 0 and double-dash flags rendered | A2 |
| S6 | H2 accepted double-dash parsing | Terminal CLI direct shell | `env -u ... /usr/bin/perl -e 'alarm 10; exec @ARGV' .omo/evidence/obsidian-sync-agent sync --server http://127.0.0.1:1 --vault-id v --vault-root /tmp --device-id d --dry-run` | PASS, exit 2 from connection refused after parsing | A3 |
| S7 | H3 targeted timestamp test | Go test | `go test -count=1 ./internal/syncagent/client -run TestClientGetChanges_readsFastAPINaiveTimestamp -v` | PASS, exit 0 | A4 |
| S8 | H4 targeted CLI exit tests | Go test | `go test -count=1 ./cmd/obsidian-sync-agent -run 'TestRunCommand\|TestRunSync' -v` | PASS, exit 0 | A5 |
| S9 | H3/H4 existing live QA transcript inspection | Existing live QA artifact grep/read | `rg -n "changes_body_after_create\|created_at\|scenario=update_and_conflict\|exit_code=1\|scenario=dry_run\|assert_dry_run_did_not_push\|cleanup_" .omo/evidence/task-8-go-sync-cli-migration-live-sync.txt` | PASS, raw naive timestamp, conflict exit 1, dry-run no-push, cleanup present | A6, A7 |
| S10 | H4 fresh require-refresh failure | Terminal CLI direct shell + disposable Python HTTP server | `env -u OBSIDIAN_LOCAL_REST_API_KEY /usr/bin/perl -e 'alarm 15; exec @ARGV' .omo/evidence/obsidian-sync-agent sync --server http://127.0.0.1:<port> --vault-id fresh --vault-root <tmp>/vault --device-id dev --require-obsidian-refresh` | PASS, exit 3 with summary and refresh-required failure | A8 |
| S11 | Cleanup/no background resources | OS process/container/listener checks | `pgrep -fl 'fake_sync_server.py\|uvicorn\|obsidian-sync-agent\|dlv'`; `docker ps --format '{{.Names}}' \| rg 'obsidian-sync-task\|task8\|global-debugging'`; `lsof -iTCP:2345 -sTCP:LISTEN -nP` | PASS, no output | A9 |

### adversarialCases

| scenario id | criterion reference | adversarial class | expected behavior | verdict | artifactRefs |
|---|---|---|---|---|---|
| ADV1 | H1 | Root command accidentally treated as help/success | No subcommand exits 2, not 0 | PASS | A2 |
| ADV2 | H2 | Help text says `--flag` but parser rejects `--flag` | Parser accepts double-dash flags; downstream network failure is distinguishable from flag parse failure | PASS | A2, A3 |
| ADV3 | H3 | False-green timestamp test selector | Correct named test must execute at least one test and pass | PASS | A4 |
| ADV4 | H3 | Stale live transcript hides raw FastAPI timestamp shape | Raw artifact must contain actual naive timestamp and adjacent successful CLI sync output | PASS | A6, A7 |
| ADV5 | H4 | Summary printing masks conflict exit | Conflict transcript must show summary plus exit 1 | PASS | A6, A7 |
| ADV6 | H4 | Summary printing masks require-refresh exit | Configured fresh probe must show summary plus refresh failure plus exit 3 | PASS | A8 |
| ADV7 | H4 | Invalid require-refresh probe could falsely pass because Obsidian refresh is not configured | Probe without Obsidian config exits 0; configured probe is the valid distinguishing check and exits 3 | PASS | A10, A8 |
| ADV8 | Cleanup | Test server/container/debugger left behind | No matching process, container, or Delve listener remains | PASS | A9 |
| ADV9 | Evidence channel | Requested tmux-style terminal transcript unavailable | Availability blocker is explicit; direct CLI transcripts still include exact commands and exit codes | PASS with channel blocker recorded | A11, A2, A3, A8 |

### artifactRefs

| id | kind | description | path |
|---|---|---|---|
| A1 | build transcript | Rebuilt Go CLI binary; command exit 0 | `/Users/ia03060_mac/workspace/qulip/obsidian-sync-go-cli-worktree/.omo/evidence/global-debugging-runtime-audit/build.txt` |
| A2 | CLI transcript | Root no args, root help, sync help, status help exact output and exit codes | `/Users/ia03060_mac/workspace/qulip/obsidian-sync-go-cli-worktree/.omo/evidence/global-debugging-runtime-audit/cli-help-direct.txt` |
| A3 | CLI transcript | Double-dash flag parser probe against closed port; exit 2 from connection refused | `/Users/ia03060_mac/workspace/qulip/obsidian-sync-go-cli-worktree/.omo/evidence/global-debugging-runtime-audit/cli-double-dash-parse-direct.txt` |
| A4 | Go test transcript | Correct targeted FastAPI naive timestamp parsing test | `/Users/ia03060_mac/workspace/qulip/obsidian-sync-go-cli-worktree/.omo/evidence/global-debugging-runtime-audit/test-client-naive-timestamp.txt` |
| A5 | Go test transcript | Targeted CLI help, conflict exit, and require-refresh exit tests | `/Users/ia03060_mac/workspace/qulip/obsidian-sync-go-cli-worktree/.omo/evidence/global-debugging-runtime-audit/test-cli-exit-behavior.txt` |
| A6 | existing live QA transcript | Full Task 8 live FastAPI QA, including raw sync changes response, conflict exit, dry-run no-push, and cleanup | `/Users/ia03060_mac/workspace/qulip/obsidian-sync-go-cli-worktree/.omo/evidence/task-8-go-sync-cli-migration-live-sync.txt` |
| A7 | inspection transcript | Grep/read extraction from existing live QA and refresh artifacts | `/Users/ia03060_mac/workspace/qulip/obsidian-sync-go-cli-worktree/.omo/evidence/global-debugging-runtime-audit/existing-live-artifact-inspection.txt` |
| A8 | CLI + fake server transcript | Fresh configured require-refresh failure, exit 3, cleanup recorded | `/Users/ia03060_mac/workspace/qulip/obsidian-sync-go-cli-worktree/.omo/evidence/global-debugging-runtime-audit/fresh-require-refresh-configured.txt` |
| A9 | cleanup transcript | Process/container/debugger listener cleanup checks | `/Users/ia03060_mac/workspace/qulip/obsidian-sync-go-cli-worktree/.omo/evidence/global-debugging-runtime-audit/cleanup-check.txt` |
| A10 | CLI + fake server transcript | Initial unconfigured require-refresh probe, showing why it was not the valid failure check | `/Users/ia03060_mac/workspace/qulip/obsidian-sync-go-cli-worktree/.omo/evidence/global-debugging-runtime-audit/fresh-require-refresh.txt` |
| A11 | tool availability transcript | `tmux` unavailable probe | `/Users/ia03060_mac/workspace/qulip/obsidian-sync-go-cli-worktree/.omo/evidence/global-debugging-runtime-audit/tmux-availability.txt` |
| A12 | Go test listing | Targeted test names available in client and CLI packages | `/Users/ia03060_mac/workspace/qulip/obsidian-sync-go-cli-worktree/.omo/evidence/global-debugging-runtime-audit/test-list.txt` |

## Exact Exit Codes

- `go build -o .omo/evidence/obsidian-sync-agent ./cmd/obsidian-sync-agent`: 0.
- `.omo/evidence/obsidian-sync-agent`: 2.
- `.omo/evidence/obsidian-sync-agent --help`: 0.
- `.omo/evidence/obsidian-sync-agent sync --help`: 0.
- `.omo/evidence/obsidian-sync-agent status --help`: 0.
- Double-dash parser probe against `127.0.0.1:1`: 2, after accepted parsing and connection refusal.
- `go test -count=1 ./internal/syncagent/client -run TestClientGetChanges_readsFastAPINaiveTimestamp -v`: 0.
- `go test -count=1 ./cmd/obsidian-sync-agent -run 'TestRunCommand|TestRunSync' -v`: 0.
- Fresh configured require-refresh probe: 3.
- `command -v tmux`: 1.

## Cleanup

Fresh fake HTTP servers were killed, temp runtime roots were removed, and cleanup checks found no matching `fake_sync_server.py`, `uvicorn`, `obsidian-sync-agent`, or `dlv` processes; no matching task Docker containers; and no Delve listener on TCP 2345.

