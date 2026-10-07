"""Tests for evals.metrics."""

import math

import pytest

from evals.metrics import (
    all_hit_at,
    ndcg_at,
    paired_bootstrap,
    recall_at,
    reciprocal_rank,
)

# Two required sources; the first has two acceptable copies.
TERMS = frozenset({"terms-6.21.9", "procurement-copy-6.21.9"})
PRICE = frozenset({"price-list-3"})
REQUIRED = [TERMS, PRICE]


def test_recall_counts_sources_not_keys() -> None:
    ranked = ["x", "terms-6.21.9", "procurement-copy-6.21.9", "y"]

    assert recall_at(ranked, REQUIRED, 3) == 0.5


def test_recall_accepts_any_copy() -> None:
    assert recall_at(["procurement-copy-6.21.9", "price-list-3"], REQUIRED, 2) == 1.0


def test_recall_looks_only_at_the_first_k() -> None:
    ranked = ["x", "y", "price-list-3", "terms-6.21.9"]

    assert recall_at(ranked, REQUIRED, 2) == 0.0
    assert recall_at(ranked, REQUIRED, 3) == 0.5
    assert recall_at(ranked, REQUIRED, 4) == 1.0
    assert recall_at(ranked, REQUIRED, 100) == 1.0  # k beyond the ranking


def test_recall_of_an_empty_ranking() -> None:
    assert recall_at([], REQUIRED, 10) == 0.0


def test_all_hit() -> None:
    ranked = ["terms-6.21.9", "x", "price-list-3"]

    assert not all_hit_at(ranked, REQUIRED, 2)
    assert all_hit_at(ranked, REQUIRED, 3)


def test_reciprocal_rank_of_the_first_hit_of_any_source() -> None:
    assert reciprocal_rank(["x", "price-list-3", "terms-6.21.9"], REQUIRED) == 0.5
    assert reciprocal_rank(["terms-6.21.9"], REQUIRED) == 1.0
    assert reciprocal_rank(["x", "y"], REQUIRED) == 0.0


def test_reciprocal_rank_beyond_k_is_zero() -> None:
    ranked = [f"x{n}" for n in range(10)] + ["price-list-3"]

    assert reciprocal_rank(ranked, REQUIRED) == 0.0
    assert reciprocal_rank(ranked, REQUIRED, k=11) == 1 / 11


def test_ndcg_of_a_perfect_ranking_is_one() -> None:
    assert ndcg_at(["procurement-copy-6.21.9", "price-list-3", "x"], REQUIRED) == 1.0
    assert ndcg_at(["price-list-3", "terms-6.21.9"], REQUIRED) == 1.0


def test_ndcg_by_hand() -> None:
    ranked = ["x", "terms-6.21.9", "y", "price-list-3"]

    # Gains at ranks 2 and 4; the ideal has them at 1 and 2.
    expected = (1 / math.log2(3) + 1 / math.log2(5)) / (1 + 1 / math.log2(3))
    assert ndcg_at(ranked, REQUIRED) == pytest.approx(expected)


def test_ndcg_counts_each_source_once() -> None:
    # The second copy of the same source gains nothing.
    ranked = ["terms-6.21.9", "procurement-copy-6.21.9"]

    assert ndcg_at(ranked, REQUIRED) == pytest.approx(1 / (1 + 1 / math.log2(3)))


def test_ndcg_ideal_is_limited_by_k() -> None:
    # Three sources but k = 1: a hit at rank 1 is the best possible.
    required = [TERMS, PRICE, frozenset({"faq-12"})]

    assert ndcg_at(["price-list-3"], required, k=1) == 1.0
    assert ndcg_at(["x", "price-list-3"], required, k=1) == 0.0


def test_a_question_without_sources_cannot_be_scored() -> None:
    for metric in (recall_at, all_hit_at, reciprocal_rank, ndcg_at):
        with pytest.raises(ValueError, match="no required sources"):
            metric(["x"], [], 10)


def test_sources_must_have_keys_and_not_share_them() -> None:
    with pytest.raises(ValueError, match="no acceptable key"):
        recall_at(["x"], [TERMS, frozenset()], 10)
    with pytest.raises(ValueError, match="share a key"):
        recall_at(["x"], [TERMS, frozenset({"terms-6.21.9"})], 10)


def test_k_must_be_positive() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        recall_at(["terms-6.21.9"], REQUIRED, 0)


def test_paired_bootstrap_mean_and_interval() -> None:
    a = [0.0, 0.5, 0.5, 1.0, 0.0, 1.0, 0.5, 0.0]
    b = [1.0, 0.5, 1.0, 1.0, 0.5, 1.0, 1.0, 0.5]

    mean, low, high = paired_bootstrap(a, b, resamples=2_000)

    assert mean == pytest.approx(0.375)
    assert low <= mean <= high
    assert low > 0  # b is better in every pair where they differ


def test_paired_bootstrap_is_deterministic() -> None:
    a = [(n * 7 % 11) / 10 for n in range(30)]
    b = [(n * 5 % 13) / 12 for n in range(30)]

    assert paired_bootstrap(a, b, seed=3) == paired_bootstrap(a, b, seed=3)
    assert paired_bootstrap(a, b, seed=3) != paired_bootstrap(a, b, seed=4)


def test_paired_bootstrap_of_a_constant_difference() -> None:
    assert paired_bootstrap([0.2, 0.4], [0.7, 0.9]) == pytest.approx((0.5, 0.5, 0.5))


def test_paired_bootstrap_needs_pairs() -> None:
    with pytest.raises(ValueError, match="paired"):
        paired_bootstrap([0.1], [0.1, 0.2])
    with pytest.raises(ValueError, match="no scores"):
        paired_bootstrap([], [])
