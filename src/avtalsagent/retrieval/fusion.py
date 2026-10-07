"""Reciprocal rank fusion of the vector and BM25 rankings, one result per section text.

What:
    `section_ranking` reduces a branch's chunk ranking to one entry per
    section text (`section_hash`), and `fuse` merges the two branches'
    section rankings into one list of `FusedSection`s, best first.

Why:
    The two branches score on scales that cannot be compared (an inner
    product of vectors, a BM25 sum), so the fusion uses only the ranks:
    reciprocal rank fusion (Cormack, Clarke and Büttcher, 2009), score =
    sum of 1 / (k + rank) over the branches, which needs no tuning but k.
    It fuses sections, not chunks: several chunks of one long section, or
    identical copies of a section (general terms repeated as a chapter of
    a procurement document), would otherwise take several places for one
    text, and the agent reads and cites whole sections.

How:
    Each branch's chunks become the ranking of their section groups, in the
    order of each group's best chunk. A group missing from a branch adds
    nothing for it. The group is shown by its best chunk across both
    branches: the one at the better group rank, the vector branch's on a tie.
    Results are sorted by score, then by that chunk's key, so equal scores
    always come out in the same order.
"""

from collections.abc import Sequence

from avtalsagent.domain.search import FusedSection, RankedChunk


def section_ranking(chunks: Sequence[RankedChunk]) -> list[RankedChunk]:
    """The first (best) chunk of each section group, in ranking order."""
    seen: set[str] = set()
    ranking: list[RankedChunk] = []
    for chunk in chunks:
        if chunk.section_hash not in seen:
            seen.add(chunk.section_hash)
            ranking.append(chunk)
    return ranking


def fuse(vector: Sequence[RankedChunk], text: Sequence[RankedChunk], k: int) -> list[FusedSection]:
    """Reciprocal rank fusion of the two branches' section groups, best first."""
    vector_ranks = {
        chunk.section_hash: (rank, chunk.key)
        for rank, chunk in enumerate(section_ranking(vector), start=1)
    }
    text_ranks = {
        chunk.section_hash: (rank, chunk.key)
        for rank, chunk in enumerate(section_ranking(text), start=1)
    }
    # The group's best chunk: the one at the better rank, the vector branch's on a tie.
    best = dict(vector_ranks)
    for section_hash, (rank, key) in text_ranks.items():
        if section_hash not in best or rank < best[section_hash][0]:
            best[section_hash] = (rank, key)
    fused: list[FusedSection] = []
    for section_hash, (_, key) in best.items():
        in_vector = vector_ranks.get(section_hash)
        in_text = text_ranks.get(section_hash)
        # Summed vector first, then text, so the float result never depends on order.
        score = 0.0
        if in_vector is not None:
            score += 1 / (k + in_vector[0])
        if in_text is not None:
            score += 1 / (k + in_text[0])
        fused.append(
            FusedSection(
                section_hash=section_hash,
                score=score,
                key=key,
                vector_rank=in_vector[0] if in_vector is not None else None,
                text_rank=in_text[0] if in_text is not None else None,
            )
        )
    return sorted(fused, key=lambda section: (-section.score, section.key))
