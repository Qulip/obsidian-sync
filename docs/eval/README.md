# Search quality evaluation

`scripts/eval_search.py` is a local, non-CI evaluation harness for vector
search quality. It reads a golden query set (see
`docs/eval/golden-queries.yaml` for the format) and runs each query through
`KnowledgeSearchService.search` directly -- the same service and repository
code the API uses -- against your configured PostgreSQL database and Ollama
embedding server. It reports Recall@K and Mean Reciprocal Rank (MRR@K)
across the set as a JSON summary on stdout.

`docs/eval/golden-queries.yaml` as committed only contains three
placeholder entries showing the expected shape (`query`, `vault_id`,
`expected_sources`, optional `expected_headings`). There is no real note
corpus in this repository, so copy the file and point `--golden` at your
own set of queries and known-relevant `source_path` values from a vault you
have actually indexed.

## Usage

```bash
export OBSIDIAN_SYNC_DATABASE_URL=postgresql+asyncpg://postgres:PASSWORD@localhost:5432/obsidian
uv run python scripts/eval_search.py --golden docs/eval/golden-queries.yaml --top-k 5
```

Requires a reachable PostgreSQL instance and a reachable Ollama server
(default `http://localhost:11434`, override via `OBSIDIAN_SYNC_OLLAMA_BASE_URL`
or the other `OBSIDIAN_SYNC_*` settings). The script exits with a clear
error message (no traceback) if either backend cannot be reached; a golden
entry referencing a vault or query that returns no results is scored as a
recall/MRR miss for that entry instead of aborting the whole run.

This harness is intentionally not wired into CI: it depends on a live
Ollama server and a vault populated with real, previously indexed notes,
neither of which exist in the automated test environment. Use it locally
after indexing a vault to spot-check ranking quality, and update
`tests/test_search_ranking.py` (deterministic fixture-based ranking
regression tests, no Ollama required) when you need something CI can run.
