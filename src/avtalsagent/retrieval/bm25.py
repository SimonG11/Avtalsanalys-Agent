"""BM25 weights of the word index: how much each stem of a chunk says about it.

What:
    `build_term_index` counts, over all chunks to index, which stems occur and
    in how many chunks, and gives each stem an id. `idf` is a stem's inverse
    document frequency and `document_weights` a chunk's BM25 weight per stem
    id. Step 6 stores the weights as the chunk's sparse vector
    (`search_chunk.term_weights`).

Why:
    PostgreSQL's `ts_rank` has no inverse document frequency, so a word in
    every clause ("enligt") counts as much as a rare one ("uppsägningstid").
    BM25 weighs each stem by how rare it is and dampens repeats and long
    chunks. With the weights stored per chunk, a question's score is a plain
    inner product: the question is the 0/1 vector of its stems, and
    `term_weights <#> question` in pgvector is minus the BM25 score. The same
    product in numpy (`evals/offline.py`) gives the same ranking.

How:
    Okapi BM25 with Lucene's idf, ln(1 + (N - df + 0.5) / (df + 0.5)), which
    is never negative, so a stem in most chunks still counts a little instead
    of lowering the score. K1 = 1.2 and B = 0.75 are the usual defaults. A
    stem repeated in the question counts once, so the query side needs only
    the ids (`search_term`), not the statistics.
"""

import math
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

K1 = 1.2  # how fast repeats of a stem in a chunk stop adding to its weight
B = 0.75  # how much a chunk's length lowers its weights (0: not at all, 1: fully)


@dataclass(frozen=True)
class TermIndex:
    """The stems of the indexed chunks and their counts."""

    ids: dict[str, int]  # stem -> 0-based id, assigned in sorted stem order
    chunk_counts: dict[str, int]  # chunks that contain the stem
    chunk_total: int
    average_length: float  # mean number of stems per chunk


def build_term_index(documents: Sequence[Sequence[str]]) -> TermIndex:
    """The term index of `documents`, each the stems of one chunk."""
    chunk_counts: Counter[str] = Counter()
    total_length = 0
    for terms in documents:
        chunk_counts.update(set(terms))
        total_length += len(terms)
    ordered = sorted(chunk_counts)
    return TermIndex(
        ids={term: term_id for term_id, term in enumerate(ordered)},
        chunk_counts={term: chunk_counts[term] for term in ordered},
        chunk_total=len(documents),
        average_length=total_length / len(documents) if documents else 0.0,
    )


def idf(index: TermIndex, term: str) -> float:
    """Lucene's inverse document frequency of `term`; never negative."""
    count = index.chunk_counts.get(term, 0)
    return math.log(1 + (index.chunk_total - count + 0.5) / (count + 0.5))


def document_weights(index: TermIndex, terms: Sequence[str]) -> dict[int, float]:
    """A chunk's BM25 weight per stem id, in id order.

    `terms` are the chunk's stems with repeats; each must be in `index` (a
    KeyError otherwise), as the index is built from the same chunks.
    """
    if not terms:
        return {}
    # The part of the denominator that depends on the chunk's length, not the stem.
    length_norm = K1 * (1 - B + B * len(terms) / index.average_length)
    weights = {
        index.ids[term]: idf(index, term) * count * (K1 + 1) / (count + length_norm)
        for term, count in Counter(terms).items()
    }
    return dict(sorted(weights.items()))
