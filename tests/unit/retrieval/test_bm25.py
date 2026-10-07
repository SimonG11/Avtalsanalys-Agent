"""Tests for avtalsagent.retrieval.bm25."""

import math

import pytest

from avtalsagent.retrieval.bm25 import (
    K1,
    B,
    TermIndex,
    build_term_index,
    document_weights,
    idf,
)
from avtalsagent.retrieval.swedish_text import terms

# Three chunks of 3, 2 and 1 stems: N = 3, average length 2.
CHUNKS = [
    ["avtal", "vite", "avtal"],
    ["avtal", "uppsägningstid"],
    ["leverantör"],
]


def test_the_parameters_are_the_usual_defaults() -> None:
    assert (K1, B) == (1.2, 0.75)


def test_build_term_index_counts_chunks_not_occurrences() -> None:
    index = build_term_index(CHUNKS)

    assert index.chunk_counts == {"avtal": 2, "leverantör": 1, "uppsägningstid": 1, "vite": 1}
    assert index.chunk_total == 3
    assert index.average_length == 2.0


def test_ids_follow_the_sorted_stems() -> None:
    index = build_term_index(CHUNKS)

    assert index.ids == {"avtal": 0, "leverantör": 1, "uppsägningstid": 2, "vite": 3}


def test_ids_do_not_depend_on_the_order_of_the_chunks() -> None:
    assert build_term_index(CHUNKS) == build_term_index(list(reversed(CHUNKS)))


def test_an_empty_index() -> None:
    index = build_term_index([])

    assert index == TermIndex(ids={}, chunk_counts={}, chunk_total=0, average_length=0.0)


def test_idf_by_hand() -> None:
    index = build_term_index(CHUNKS)

    # ln(1 + (N - df + 0.5) / (df + 0.5))
    assert idf(index, "avtal") == pytest.approx(math.log(1 + 1.5 / 2.5))
    assert idf(index, "vite") == pytest.approx(math.log(1 + 2.5 / 1.5))
    assert idf(index, "saknas") == pytest.approx(math.log(1 + 3.5 / 0.5))


def test_idf_of_a_stem_in_every_chunk_is_small_but_positive() -> None:
    index = build_term_index([["avtal"], ["avtal", "vite"], ["avtal"]])

    assert idf(index, "avtal") == pytest.approx(math.log(1 + 0.5 / 3.5))
    assert 0 < idf(index, "avtal") < idf(index, "vite")


def test_document_weights_by_hand() -> None:
    index = build_term_index(CHUNKS)

    # The first chunk: length 3, so K1 * (1 - B + B * 3 / 2) = 1.2 * 1.375 = 1.65.
    # "avtal" twice: idf * 2 * 2.2 / (2 + 1.65); "vite" once: idf * 2.2 / (1 + 1.65).
    weights = document_weights(index, CHUNKS[0])

    assert list(weights) == [0, 3]
    assert weights[0] == pytest.approx(math.log(1.6) * 4.4 / 3.65)
    assert weights[3] == pytest.approx(math.log(1 + 2.5 / 1.5) * 2.2 / 2.65)


def test_a_shorter_chunk_weighs_a_stem_more() -> None:
    index = build_term_index(CHUNKS)

    # "avtal" once in a chunk of 2 (the average): K1 * (1 - B + B) = 1.2.
    weights = document_weights(index, CHUNKS[1])

    assert weights[0] == pytest.approx(math.log(1.6) * 2.2 / 2.2)
    assert weights[0] > document_weights(index, ["avtal", "vite", "vite"])[0]


def test_repeats_add_less_and_less() -> None:
    index = build_term_index([["vite"] * n + ["x"] * (10 - n) for n in range(1, 11)] + [["y"]])
    one, two, three = (
        document_weights(index, ["vite"] * n + ["x"] * (10 - n))[index.ids["vite"]]
        for n in (1, 2, 3)
    )

    assert one < two < three
    assert three - two < two - one


def test_a_chunk_without_stems_has_no_weights() -> None:
    assert document_weights(build_term_index(CHUNKS), []) == {}


def test_a_stem_outside_the_index_is_an_error() -> None:
    with pytest.raises(KeyError):
        document_weights(build_term_index(CHUNKS), ["saknas"])


def test_weights_of_analysed_pilot_text() -> None:
    # 18309f4961d3 page 26 and 0a5491b1398e page 27.
    chunks = [
        terms("Ramavtalet IT-drift ska användas när Avropsberättigads behov omfattar tjänster."),
        terms("6.21.9 Ramavtalsleverantörens uppsägningsrätt"),
    ]
    index = build_term_index(chunks)

    assert set(document_weights(index, chunks[1])) == {
        index.ids["6.21.9"],
        index.ids["ramavtalsleverantör"],
        index.ids["uppsägningsrät"],
    }
    assert "ska" not in index.ids
