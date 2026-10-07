"""Scores of a search ranking against the sections a test question needs.

What:
    `recall_at`, `all_hit_at`, `reciprocal_rank` and `ndcg_at` score one
    question's ranking; `paired_bootstrap` says whether one variant of the
    search beats another over all questions by more than chance.

Why:
    The agent reads and cites sections, so a question's gold answer is a list
    of required sources, each a section. A source counts as found when any
    acceptable copy of it is ranked (the general terms appear word for word
    in several documents, and the fusion shows a group of copies once). With
    some 30 questions, a difference of a few points can be noise; the
    bootstrap gives the interval of the difference.

How:
    `ranked` holds the result groups' keys, best first (whatever key the
    caller uses for a group, such as its section hash); each required source
    is the set of keys that satisfy it. Required sources must not share a
    key, so each ranked key satisfies at most one source and every score
    stays between 0 and 1. A question without required sources cannot be
    scored (a ValueError): leave it out of the average.
"""

import math
from collections.abc import Sequence

import numpy as np


def recall_at(ranked: Sequence[str], required: Sequence[frozenset[str]], k: int) -> float:
    """The share of required sources found in the first `k` results."""
    ranks = _first_ranks(ranked, required, k)
    return sum(rank is not None for rank in ranks) / len(required)


def all_hit_at(ranked: Sequence[str], required: Sequence[frozenset[str]], k: int) -> bool:
    """Whether every required source is found in the first `k` results."""
    return all(rank is not None for rank in _first_ranks(ranked, required, k))


def reciprocal_rank(
    ranked: Sequence[str], required: Sequence[frozenset[str]], k: int = 10
) -> float:
    """1 / the rank of the first result that is any required source; 0 if none in `k`."""
    ranks = [rank for rank in _first_ranks(ranked, required, k) if rank is not None]
    return 1 / min(ranks) if ranks else 0.0


def ndcg_at(ranked: Sequence[str], required: Sequence[frozenset[str]], k: int = 10) -> float:
    """Normalised discounted cumulative gain in the first `k` results.

    Each required source gains 1 where it is first found (later copies gain
    nothing), discounted by log2(1 + rank); the ideal ranks every source
    first.
    """
    ranks = _first_ranks(ranked, required, k)
    gain = sum(1 / math.log2(1 + rank) for rank in ranks if rank is not None)
    ideal = sum(1 / math.log2(1 + rank) for rank in range(1, min(k, len(required)) + 1))
    return gain / ideal


def paired_bootstrap(
    a: Sequence[float], b: Sequence[float], resamples: int = 10_000, seed: int = 0
) -> tuple[float, float, float]:
    """The mean of b - a over paired scores, and its 95% bootstrap interval.

    Resamples the questions with replacement `resamples` times and returns
    (mean, 2.5th percentile, 97.5th percentile) of the mean difference. An
    interval that excludes 0 means b differs from a beyond chance. The same
    seed gives the same interval.
    """
    if len(a) != len(b):
        raise ValueError(f"{len(a)} scores for a but {len(b)} for b; they must be paired")
    if not a:
        raise ValueError("no scores to compare")
    if resamples < 1:
        raise ValueError(f"cannot resample {resamples} times")
    differences = np.asarray(b, dtype=np.float64) - np.asarray(a, dtype=np.float64)
    rng = np.random.default_rng(seed)
    samples = rng.integers(0, len(differences), size=(resamples, len(differences)))
    means = differences[samples].mean(axis=1)
    low, high = np.percentile(means, [2.5, 97.5])
    return float(differences.mean()), float(low), float(high)


def _first_ranks(
    ranked: Sequence[str], required: Sequence[frozenset[str]], k: int
) -> list[int | None]:
    """Each required source's 1-based rank of first appearance in ranked[:k], or None."""
    if k < 1:
        raise ValueError(f"k must be at least 1, not {k}")
    if not required:
        raise ValueError("the question has no required sources")
    if any(not source for source in required):
        raise ValueError("a required source has no acceptable key")
    if sum(len(source) for source in required) != len(frozenset().union(*required)):
        raise ValueError("two required sources share a key")
    first: dict[str, int] = {}
    for rank, key in enumerate(ranked[:k], start=1):
        first.setdefault(key, rank)
    return [
        min((first[key] for key in source if key in first), default=None) for source in required
    ]
