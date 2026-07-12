"""Unit tests for the domain Reciprocal Rank Fusion (RRF) merge helper.

These are pure-function tests -- no database or async fixtures required --
covering the merge behavior used by KnowledgeSearchService to combine
vector and lexical candidate lists (see docs/vector-search-quality-analysis
hybrid search follow-up).
"""

from obsidian_sync.domain.search import DEFAULT_RRF_K, reciprocal_rank_fusion


def test_default_k_is_60() -> None:
    assert DEFAULT_RRF_K == 60


def test_empty_input_produces_empty_result() -> None:
    assert reciprocal_rank_fusion(()) == []
    assert reciprocal_rank_fusion(([], [])) == []


def test_single_list_preserves_its_own_rank_order() -> None:
    merged = reciprocal_rank_fusion(([5, 3, 9],))

    assert merged == [5, 3, 9]


def test_overlapping_candidate_outranks_single_list_candidates() -> None:
    vector_ranked = [1, 2, 3]
    lexical_ranked = [2, 4]

    merged = reciprocal_rank_fusion((vector_ranked, lexical_ranked))

    # Chunk 2 appears in both lists (rank 2 in vector, rank 1 in lexical),
    # so its summed RRF score should beat every chunk seen in only one leg.
    assert merged[0] == 2
    assert set(merged) == {1, 2, 3, 4}


def test_rrf_scores_use_k_equal_60_weighting() -> None:
    merged = reciprocal_rank_fusion(([1, 2], [2, 3]), k=60)

    expected_scores = {
        1: 1 / 61,
        2: 1 / 62 + 1 / 61,
        3: 1 / 62,
    }
    expected_order = sorted(
        expected_scores, key=lambda identifier: expected_scores[identifier],
        reverse=True,
    )

    assert expected_order == [2, 1, 3]
    assert merged == expected_order


def test_non_overlapping_candidates_keep_relative_rank_order() -> None:
    # Neither list shares any identifier, so within each list, lower rank
    # (earlier position) always yields a higher score at any fixed k.
    merged = reciprocal_rank_fusion(([10, 11], [20, 21]))

    assert merged.index(10) < merged.index(11)
    assert merged.index(20) < merged.index(21)


def test_ties_are_broken_by_first_seen_order_across_lists() -> None:
    # Two disjoint singleton lists both rank their member #1, producing an
    # identical RRF score; the first list's candidate should win the tie.
    merged = reciprocal_rank_fusion(([10], [20]))

    assert merged == [10, 20]


def test_custom_k_changes_relative_weighting() -> None:
    # A much smaller k widens the gap between rank 1 and rank 2 within a
    # single list, but ordering for a single list is unaffected by k.
    merged_default = reciprocal_rank_fusion(([1, 2, 3],), k=60)
    merged_small_k = reciprocal_rank_fusion(([1, 2, 3],), k=1)

    assert merged_default == merged_small_k == [1, 2, 3]
