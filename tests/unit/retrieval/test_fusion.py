"""Tests for avtalsagent.retrieval.fusion."""

import random

from avtalsagent.domain.search import ChunkKey, FusedSection, RankedChunk
from avtalsagent.retrieval.fusion import fuse, section_ranking

# Two files of the pilot that both hold the general terms' "6.21.9
# Ramavtalsleverantörens uppsägningsrätt" (page 27): copies of one section.
COPY_1 = "0a5491b1398e" + "0" * 52
COPY_2 = "28ca7018a608" + "0" * 52
OTHER = "18309f4961d3" + "0" * 52


def chunk(sha256: str, section: int, position: int, section_hash: str) -> RankedChunk:
    return RankedChunk(ChunkKey(sha256, section, position), section_hash)


def test_section_ranking_keeps_each_groups_best_chunk_in_order() -> None:
    a1, b1, a2, c1, b2 = (
        chunk(OTHER, 1, 0, "a"),
        chunk(OTHER, 2, 0, "b"),
        chunk(OTHER, 1, 1, "a"),
        chunk(OTHER, 3, 0, "c"),
        chunk(OTHER, 2, 1, "b"),
    )

    assert section_ranking([a1, b1, a2, c1, b2]) == [a1, b1, c1]
    assert section_ranking([]) == []


def test_copies_in_different_files_are_one_group() -> None:
    first_copy = chunk(COPY_2, 40, 0, "uppsägningsrätt")
    second_copy = chunk(COPY_1, 40, 0, "uppsägningsrätt")
    other = chunk(OTHER, 7, 0, "annat")

    assert section_ranking([first_copy, other, second_copy]) == [first_copy, other]
    fused = fuse([first_copy, other, second_copy], [second_copy], k=60)
    assert [section.section_hash for section in fused] == ["uppsägningsrätt", "annat"]
    assert fused[0].vector_rank == 1  # the group's place, not the second copy's 3
    assert fused[1].vector_rank == 2


def test_fuse_by_hand() -> None:
    a1, a2, a3 = chunk(OTHER, 1, 0, "a"), chunk(OTHER, 1, 1, "a"), chunk(OTHER, 1, 2, "a")
    b1 = chunk(OTHER, 2, 0, "b")
    c1, c2 = chunk(OTHER, 3, 0, "c"), chunk(OTHER, 3, 1, "c")

    # Vector groups: a 1, b 2, c 3. Text groups: c 1, a 2.
    fused = fuse([a1, b1, a2, c1], [c2, a3], k=60)

    assert fused == [
        FusedSection("a", 1 / 61 + 1 / 62, a1.key, vector_rank=1, text_rank=2),
        FusedSection("c", 1 / 63 + 1 / 61, c2.key, vector_rank=3, text_rank=1),
        FusedSection("b", 1 / 62, b1.key, vector_rank=2, text_rank=None),
    ]


def test_k_weighs_one_top_rank_against_two_lower_ones() -> None:
    a, b = chunk(OTHER, 1, 0, "a"), chunk(OTHER, 2, 0, "b")
    fillers = [chunk(OTHER, n, 0, f"f{n}") for n in range(10, 16)]
    vector = [a, *fillers[:2], b]  # a 1, b 4
    text = [*fillers[3:], b]  # b 4

    # k = 1: a has 1/2, b 1/5 + 1/5. k = 60: a has 1/61, b 1/64 + 1/64.
    def order(k: int) -> list[str]:
        return [s.section_hash for s in fuse(vector, text, k) if s.section_hash in {"a", "b"}]

    assert order(1) == ["a", "b"]
    assert order(60) == ["b", "a"]


def test_the_group_is_shown_by_its_better_ranked_chunk() -> None:
    vector_best = chunk(COPY_1, 40, 0, "g")
    text_best = chunk(COPY_2, 40, 0, "g")
    filler = [chunk(OTHER, n, 0, f"f{n}") for n in range(3)]

    # Vector rank 4, text rank 1: the text branch's chunk.
    assert fuse([*filler, vector_best], [text_best], k=60)[0].key == text_best.key
    # Vector rank 1, text rank 4: the vector branch's chunk.
    assert fuse([vector_best], [*filler, text_best], k=60)[0].key == vector_best.key


def test_the_vector_branch_wins_a_tie_in_rank() -> None:
    vector_best = chunk(COPY_2, 40, 0, "g")  # the higher key
    text_best = chunk(COPY_1, 40, 0, "g")

    (fused,) = fuse([vector_best], [text_best], k=60)

    assert fused.key == vector_best.key
    assert (fused.vector_rank, fused.text_rank) == (1, 1)


def test_equal_scores_are_ordered_by_key() -> None:
    low = chunk(COPY_1, 1, 0, "low")
    high = chunk(COPY_2, 1, 0, "high")

    # Both rank 1 in one branch: the same score, so the lower key comes first,
    # whichever branch found it.
    assert [section.key for section in fuse([high], [low], k=60)] == [low.key, high.key]
    assert [section.key for section in fuse([low], [high], k=60)] == [low.key, high.key]


def test_fuse_is_deterministic() -> None:
    rng = random.Random(7)
    chunks = [chunk(OTHER, n // 3, n % 3, f"s{n // 3}") for n in range(60)]
    vector = rng.sample(chunks, 40)
    text = rng.sample(chunks, 40)

    results = {tuple(fuse(vector, text, k=60)) for _ in range(5)}

    assert len(results) == 1
    (result,) = results
    assert [(-s.score, s.key) for s in result] == sorted((-s.score, s.key) for s in result)


def test_one_empty_branch() -> None:
    a, b = chunk(OTHER, 1, 0, "a"), chunk(OTHER, 2, 0, "b")

    assert fuse([], [a, b], k=60) == [
        FusedSection("a", 1 / 61, a.key, vector_rank=None, text_rank=1),
        FusedSection("b", 1 / 62, b.key, vector_rank=None, text_rank=2),
    ]
    assert fuse([], [], k=60) == []
