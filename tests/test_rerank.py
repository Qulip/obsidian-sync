"""Unit tests for the domain listwise rerank prompt/parse helpers.

These are pure-function tests -- no database, no Ollama server -- covering
`build_rerank_prompt` and `parse_rerank_order`, which back
KnowledgeSearchService's optional rerank step (see
docs/vector-search-quality-analysis.md P2 rerank follow-up).
"""

from obsidian_sync.domain.rerank import (
    RerankCandidate,
    build_rerank_prompt,
    parse_rerank_order,
)


def _candidates(count: int) -> list[RerankCandidate]:
    return [
        RerankCandidate(
            rank=index + 1,
            source_path=f'notes/{index}.md',
            heading='Heading' if index % 2 == 0 else None,
            content=f'Content body number {index}.',
        )
        for index in range(count)
    ]


def test_build_rerank_prompt_includes_query_and_numbered_candidates() -> None:
    prompt = build_rerank_prompt('how to deploy', _candidates(3))

    assert 'how to deploy' in prompt
    assert '1. [notes/0.md] Heading' in prompt
    assert '2. [notes/1.md] (no heading)' in prompt
    assert '3. [notes/2.md] Heading' in prompt
    assert 'JSON array' in prompt


def test_build_rerank_prompt_truncates_long_content() -> None:
    long_content = 'x' * 1000
    candidates = [
        RerankCandidate(rank=1, source_path='a.md', heading=None, content=long_content)
    ]

    prompt = build_rerank_prompt('query', candidates)

    assert long_content not in prompt
    assert ('x' * 500) in prompt


def test_parse_rerank_order_with_clean_json_array() -> None:
    order = parse_rerank_order('[3, 1, 2]', candidate_count=3)

    assert order == [2, 0, 1]


def test_parse_rerank_order_with_code_fence_and_prose() -> None:
    response = (
        "Here is the ranking:\n```json\n[2, 1, 3]\n```\nHope that helps!"
    )

    order = parse_rerank_order(response, candidate_count=3)

    assert order == [1, 0, 2]


def test_parse_rerank_order_drops_out_of_range_and_duplicate_numbers() -> None:
    # 99 is out of range, 2 is duplicated -- both should be dropped, and the
    # untouched candidate (3) should be appended in its original position.
    order = parse_rerank_order('[2, 99, 2, 1]', candidate_count=3)

    assert order == [1, 0, 2]


def test_parse_rerank_order_appends_missing_candidates_in_original_order() -> None:
    order = parse_rerank_order('[2]', candidate_count=4)

    assert order == [1, 0, 2, 3]


def test_parse_rerank_order_with_garbage_response_falls_back_to_identity() -> None:
    order = parse_rerank_order('I cannot help with that request.', candidate_count=3)

    assert order == [0, 1, 2]


def test_parse_rerank_order_with_empty_candidates_returns_empty() -> None:
    assert parse_rerank_order('[1, 2, 3]', candidate_count=0) == []


def test_parse_rerank_order_ignores_non_integer_and_boolean_entries() -> None:
    order = parse_rerank_order('[true, "2", 1, 2.5, 2]', candidate_count=2)

    assert order == [0, 1]
