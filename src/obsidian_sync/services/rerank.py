"""Optional listwise LLM rerank step for hybrid search results.

Ollama has no native rerank endpoint, so this asks a chat/completion model
to reorder a numbered candidate list and parses the reply back into a
permutation via `obsidian_sync.domain.rerank`. Any failure here (request
error, timeout, malformed response) must never break search -- callers get
`None` back and are expected to fall back to the original ranking.
"""

from collections.abc import Sequence

from obsidian_sync.clients.ollama import OllamaClient
from obsidian_sync.domain.rerank import (
    RerankCandidate,
    build_rerank_prompt,
    parse_rerank_order,
)
from obsidian_sync.repositories.search import SearchResultRecord


async def rerank_records(
    *,
    ollama_client: OllamaClient,
    model: str,
    query: str,
    records: Sequence[SearchResultRecord],
) -> list[SearchResultRecord] | None:
    """Return `records` reordered by LLM relevance, or `None` on any failure."""
    if not records:
        return None

    candidates = [
        RerankCandidate(
            rank=index + 1,
            source_path=record.source_path,
            heading=(
                ' > '.join(record.heading_path) if record.heading_path else None
            ),
            content=record.content,
        )
        for index, record in enumerate(records)
    ]
    prompt = build_rerank_prompt(query, candidates)

    try:
        response_text = await ollama_client.generate(model=model, prompt=prompt)
    except Exception:  # rerank must never fail search itself
        return None

    order = parse_rerank_order(response_text, len(records))
    return [records[position] for position in order]
