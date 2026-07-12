"""Pure prompt-building and response-parsing helpers for listwise LLM rerank.

Kept side-effect free so the rerank step can be unit tested without a
running Ollama server: `build_rerank_prompt` turns ranked candidates into a
numbered listwise prompt, and `parse_rerank_order` turns the model's
(possibly messy) reply back into a validated 0-indexed permutation of the
original candidate positions. Any candidate the model omits, and any
response that fails to parse at all, degrades gracefully rather than
raising -- callers use an empty/partial parse to fall back to the original
order for the affected positions.
"""

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass

CONTENT_PREVIEW_CHARS = 500

_JSON_ARRAY_PATTERN = re.compile(r'\[[^\[\]]*\]', re.DOTALL)


@dataclass(frozen=True, slots=True)
class RerankCandidate:
    rank: int
    source_path: str
    heading: str | None
    content: str


def build_rerank_prompt(query: str, candidates: Sequence[RerankCandidate]) -> str:
    """Build a listwise rerank prompt asking for a JSON array of numbers."""
    lines = [
        'You are ranking search results by relevance to a query.',
        f'Query: {query}',
        '',
        'Candidates:',
    ]
    for candidate in candidates:
        heading = candidate.heading or '(no heading)'
        preview = candidate.content[:CONTENT_PREVIEW_CHARS]
        lines.append(f'{candidate.rank}. [{candidate.source_path}] {heading}')
        lines.append(preview)
    lines.append('')
    lines.append(
        'Return ONLY a JSON array of the candidate numbers above, ordered '
        'from most to least relevant to the query. Output nothing else -- '
        'no explanation, no code fences.'
    )
    return '\n'.join(lines)


def parse_rerank_order(response_text: str, candidate_count: int) -> list[int]:
    """Parse a listwise rerank reply into a 0-indexed candidate order.

    Tolerant of code fences and surrounding prose: extracts the first
    `[...]` array found in the text. Numbers outside 1..candidate_count and
    repeated numbers are dropped; any candidate the model never mentioned
    is appended in its original rank order, so the result is always a full
    permutation of `range(candidate_count)`.
    """
    order = _extract_order(response_text, candidate_count)
    seen = set(order)
    for position in range(candidate_count):
        rank = position + 1
        if rank not in seen:
            order.append(rank)
            seen.add(rank)
    return [rank - 1 for rank in order]


def _extract_order(response_text: str, candidate_count: int) -> list[int]:
    match = _JSON_ARRAY_PATTERN.search(response_text)
    if match is None:
        return []
    try:
        raw = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    if not isinstance(raw, list):
        return []

    order: list[int] = []
    seen: set[int] = set()
    for item in raw:
        if not isinstance(item, int) or isinstance(item, bool):
            continue
        if item < 1 or item > candidate_count:
            continue
        if item in seen:
            continue
        order.append(item)
        seen.add(item)
    return order
