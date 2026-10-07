"""Tests for evals.offline.

The chunk texts are verbatim sentences from the pilot's parsed documents
(data/parsed, 2026-10-06), cited by the file's sha256[:12] and page.
"""

import hashlib
import math
from collections.abc import Sequence

import numpy as np
import pytest

from avtalsagent.domain.search import (
    ChunkKey,
    DocumentScope,
    IndexedChunk,
    IndexPlan,
    RankedChunk,
    SearchFilters,
)
from avtalsagent.retrieval import bm25
from avtalsagent.retrieval.fusion import fuse
from avtalsagent.retrieval.swedish_text import query_terms, terms
from evals.offline import OfflineIndex, SparseWeights

# Files: the general terms of IT-drift (shared by the procurement), Crayon's
# supplier card, another supplier's card in the same procurement, a file of
# another procurement, and a file linked from a page of no procurement.
TERMS_FILE = "0a5491b1398e" + "0" * 52
CRAYON = "185872a6bb90" + "0" * 52
OTHER_SUPPLIER = "65d611d12eab" + "0" * 52
OTHER_PROCUREMENT = "4b6c2a533fae" + "0" * 52
NO_PROCUREMENT = "07629941f3e6" + "0" * 52

PROCUREMENT = "23.3-2649-2022"
AGREEMENT = "23.3-2649-2022-003"

TEXTS = {
    # 0a5491b1398e page 27
    ChunkKey(TERMS_FILE, 40, 0): "6.21.9 Ramavtalsleverantörens uppsägningsrätt",
    # 18309f4961d3 page 26
    ChunkKey(TERMS_FILE, 41, 0): (
        "Ramavtalet IT-drift ska användas när Avropsberättigads behov omfattar tjänster ur "
        "minst två av dessa block."
    ),
    # 185872a6bb90 page 3
    ChunkKey(CRAYON, 1, 0): (
        "Ramavtal med avtalsnummer 23.3.2649-22-003, har träffats för Avropsberättigades "
        "räkning, mellan Statens inköpscentral vid Kammarkollegiet och Crayon AB."
    ),
    # 20c753d88340 page 17
    ChunkKey(OTHER_SUPPLIER, 1, 0): (
        "Fråga 1: I scenariot konsult är anställd hos underleverantör vem hos "
        "underleverantören skall då skriva på"
    ),
    # 4b6c2a533fae page 6
    ChunkKey(OTHER_PROCUREMENT, 1, 0): (
        "- Ramavtalet för område 1 - Verksamhetens IT-behov är giltigt från och med "
        "2025-08-19 till och med 2029-08-18."
    ),
    # 07629941f3e6 page 9
    ChunkKey(NO_PROCUREMENT, 1, 0): (
        "När detta Säkerhetsskyddsavtal har upphört ska Verksamhetsutövaren skyndsamt "
        "avanmäla det till Säkerhetspolisen."
    ),
}

SCOPES = {
    TERMS_FILE: DocumentScope(
        TERMS_FILE, ("IT-drift",), (PROCUREMENT,), (), ("IT-drift",), "general_terms"
    ),
    CRAYON: DocumentScope(
        CRAYON, ("IT-drift",), (PROCUREMENT,), (AGREEMENT,), ("Crayon AB",), "supplier_agreement"
    ),
    OTHER_SUPPLIER: DocumentScope(
        OTHER_SUPPLIER,
        ("IT-drift",),
        (PROCUREMENT,),
        ("23.3-2649-2022-001",),
        ("Atea",),
        "supplier_agreement",
    ),
    OTHER_PROCUREMENT: DocumentScope(
        OTHER_PROCUREMENT, ("Programvaror",), ("23.3-9999-2024",), (), ("Programvaror",)
    ),
    NO_PROCUREMENT: DocumentScope(NO_PROCUREMENT, (), (), (), ("Säkerhetsskydd",)),
}
AGREEMENT_PROCUREMENTS = {AGREEMENT: PROCUREMENT, "23.3-2649-2022-001": PROCUREMENT}


def indexed(key: ChunkKey, text: str, section_hash: str | None = None) -> IndexedChunk:
    return IndexedChunk(
        key=key,
        embedded_text=text,
        text_hash=hashlib.sha256(text.encode()).hexdigest(),
        section_hash=section_hash or hashlib.sha256(text.encode()).hexdigest(),
        terms=tuple(terms(text)),
    )


def unit(*values: float) -> list[float]:
    norm = math.sqrt(sum(value * value for value in values))
    return [value / norm for value in values]


def embeddings_for(chunks: Sequence[IndexedChunk]) -> dict[str, list[float]]:
    """A fixed, distinct unit vector per text (3 dimensions)."""
    return {chunk.text_hash: unit(1.0, 0.1 * n, 0.05 * n * n) for n, chunk in enumerate(chunks)}


def pilot_index(chunks: Sequence[IndexedChunk] | None = None) -> OfflineIndex:
    chunks = chunks or [indexed(key, text) for key, text in TEXTS.items()]
    return OfflineIndex.build(
        IndexPlan(chunks=tuple(chunks), held_back=0),
        embeddings_for(chunks),
        SCOPES,
        AGREEMENT_PROCUREMENTS,
    )


def keys(ranked: Sequence[RankedChunk]) -> list[ChunkKey]:
    return [chunk.key for chunk in ranked]


ALL = SearchFilters()


def test_rows_are_in_key_order_whatever_the_plan_order() -> None:
    chunks = [indexed(key, text) for key, text in TEXTS.items()]

    index = pilot_index(list(reversed(chunks)))

    assert list(index.keys) == sorted(TEXTS)
    assert index.embeddings.dtype == np.float32
    assert index.embeddings.shape == (len(TEXTS), 3)


def test_bm25_scores_are_the_weights_summed_in_float32() -> None:
    chunks = [indexed(key, text) for key, text in TEXTS.items()]
    index = pilot_index(chunks)
    stems = query_terms("Hur länge är ramavtalet giltigt och vem ska skriva på?")
    term_index = bm25.build_term_index([chunk.terms for chunk in sorted(chunks, key=_key)])

    expected: dict[ChunkKey, np.float32] = {}
    for chunk in chunks:
        weights = bm25.document_weights(term_index, chunk.terms)
        ids = sorted({term_index.ids[stem] for stem in stems if stem in term_index.ids})
        total = np.float32(0)
        for term_id in ids:  # pgvector's order: ascending id, float32
            total = np.float32(total + np.float32(weights.get(term_id, 0.0)))
        expected[chunk.key] = total

    term_ids = [index.term_ids[stem] for stem in stems if stem in index.term_ids]
    scores = index.weights.scores(term_ids, len(index.keys))
    assert [scores[row] for row in range(len(index.keys))] == [expected[key] for key in index.keys]
    ranked = keys(index.text_candidates(stems, ALL, 10))
    assert ranked == sorted(
        (key for key in expected if expected[key] > 0), key=lambda key: (-expected[key], key)
    )


def test_text_candidates_only_with_a_positive_score() -> None:
    index = pilot_index()

    assert keys(index.text_candidates(query_terms("uppsägningsrätt"), ALL, 10)) == [
        ChunkKey(TERMS_FILE, 40, 0)
    ]
    assert index.text_candidates(query_terms("okänt ord"), ALL, 10) == []
    assert index.text_candidates([], ALL, 10) == []


def test_text_candidates_count_a_repeated_stem_once() -> None:
    index = pilot_index()
    once = index.text_candidates(["ramavtal"], ALL, 10)

    assert index.text_candidates(["ramavtal", "ramavtal"], ALL, 10) == once
    rows = len(index.keys)
    assert np.array_equal(index.weights.scores([0, 0], rows), index.weights.scores([0], rows))


def test_the_definite_form_finds_the_bare_form() -> None:
    index = pilot_index()

    found = keys(index.text_candidates(query_terms("Vad gäller enligt ramavtalet?"), ALL, 10))

    assert ChunkKey(CRAYON, 1, 0) in found  # the chunk says "Ramavtal"


def test_text_ties_are_broken_by_key() -> None:
    first, second = ChunkKey(CRAYON, 1, 0), ChunkKey(CRAYON, 2, 0)
    same = "Uppsägning av avtalet"
    chunks = [indexed(second, same, "x"), indexed(first, same, "y")]
    chunks.append(indexed(ChunkKey(TERMS_FILE, 1, 0), "Något helt annat"))

    index = pilot_index(chunks)

    assert keys(index.text_candidates(["avtal"], ALL, 10)) == [first, second]


def test_vector_candidates_rank_by_inner_product() -> None:
    chunks = [indexed(key, text) for key, text in TEXTS.items()]
    index = pilot_index(chunks)
    query = unit(0.2, 1.0, 0.0)

    expected = sorted(
        TEXTS, key=lambda key: (-float(index.embeddings[index.keys.index(key)] @ query), key)
    )
    assert keys(index.vector_candidates(query, ALL, 10)) == expected
    assert keys(index.vector_candidates(query, ALL, 2)) == expected[:2]
    assert index.vector_candidates(query, ALL, 0) == []


def test_vector_ties_are_broken_by_key() -> None:
    first, second = ChunkKey(CRAYON, 1, 0), ChunkKey(CRAYON, 2, 0)
    chunks = [indexed(second, "Text två"), indexed(first, "Text ett")]
    plan = IndexPlan(tuple(chunks), held_back=0)
    same = {chunk.text_hash: unit(1.0, 1.0) for chunk in chunks}

    index = OfflineIndex.build(plan, same, SCOPES, {})

    assert keys(index.vector_candidates(unit(1.0, 0.0), ALL, 10)) == [first, second]


def test_a_vector_of_the_wrong_dimension_is_refused() -> None:
    with pytest.raises(ValueError, match="dimensions"):
        pilot_index().vector_candidates([1.0, 0.0], ALL, 10)


def test_framework_area_filter() -> None:
    index = pilot_index()

    found = keys(index.vector_candidates(unit(1, 0, 0), SearchFilters("Programvaror"), 10))

    assert found == [ChunkKey(OTHER_PROCUREMENT, 1, 0)]


def test_procurement_filter() -> None:
    index = pilot_index()

    filters = SearchFilters(procurement_number=PROCUREMENT)

    found = index.vector_candidates(unit(1, 0, 0), filters, 10)

    assert {key.sha256 for key in keys(found)} == {TERMS_FILE, CRAYON, OTHER_SUPPLIER}


def test_agreement_filter_includes_the_procurements_shared_files() -> None:
    index = pilot_index()
    filters = SearchFilters(agreement_number=AGREEMENT)

    vector = keys(index.vector_candidates(unit(1, 0, 0), filters, 10))
    text = keys(index.text_candidates(query_terms("ramavtal uppsägningsrätt"), filters, 10))

    # Crayon's card and the general terms, not the other supplier's card, the other
    # procurement's file or the file of no procurement.
    assert {key.sha256 for key in vector} == {CRAYON, TERMS_FILE}
    assert {key.sha256 for key in text} == {CRAYON, TERMS_FILE}


def test_an_agreement_outside_the_register_matches_only_its_own_card() -> None:
    chunks = [indexed(key, text) for key, text in TEXTS.items()]
    index = OfflineIndex.build(IndexPlan(tuple(chunks), 0), embeddings_for(chunks), SCOPES, {})
    filters = SearchFilters(agreement_number=AGREEMENT)

    found = keys(index.vector_candidates(unit(1, 0, 0), filters, 10))

    assert {key.sha256 for key in found} == {CRAYON}


def test_document_type_filter() -> None:
    index = pilot_index()

    cards = keys(
        index.vector_candidates(
            unit(1, 0, 0), SearchFilters(document_type="supplier_agreement"), 10
        )
    )
    crayon = SearchFilters(agreement_number=AGREEMENT, document_type="supplier_agreement")

    assert {key.sha256 for key in cards} == {CRAYON, OTHER_SUPPLIER}
    assert {key.sha256 for key in keys(index.vector_candidates(unit(1, 0, 0), crayon, 10))} == {
        CRAYON
    }
    # A file of no type is found only without a type filter.
    assert index.vector_candidates(unit(1, 0, 0), SearchFilters(document_type="template"), 10) == []


def test_filters_combine() -> None:
    index = pilot_index()

    both = SearchFilters(framework_area="Programvaror", agreement_number=AGREEMENT)

    assert index.vector_candidates(unit(1, 0, 0), both, 10) == []


def test_a_file_of_no_area_or_procurement_is_found_only_without_filters() -> None:
    index = pilot_index()
    stems = query_terms("Säkerhetsskyddsavtal")

    assert keys(index.text_candidates(stems, ALL, 10)) == [ChunkKey(NO_PROCUREMENT, 1, 0)]
    assert index.text_candidates(stems, SearchFilters(framework_area="IT-drift"), 10) == []
    assert index.text_candidates(stems, SearchFilters(agreement_number=AGREEMENT), 10) == []


def test_build_refuses_a_file_without_a_scope() -> None:
    chunks = [indexed(key, text) for key, text in TEXTS.items()]
    scopes = {sha256: scope for sha256, scope in SCOPES.items() if sha256 != CRAYON}

    with pytest.raises(ValueError, match="1 indexed files have no scope"):
        OfflineIndex.build(IndexPlan(tuple(chunks), 0), embeddings_for(chunks), scopes, {})


def test_search_fuses_the_two_branches() -> None:
    index = pilot_index()
    query = unit(1, 0.3, 0.1)
    stems = query_terms("Ramavtalsleverantörens uppsägningsrätt enligt ramavtalet")

    result = index.search(query, stems, ALL, limit=3, candidates=4, rrf_k=60)

    vector, text = index.vector_candidates(query, ALL, 4), index.text_candidates(stems, ALL, 4)
    assert result == fuse(vector, text, 60)[:3]
    assert len(result) == 3


def test_search_returns_copies_once() -> None:
    copy_1, copy_2 = ChunkKey(TERMS_FILE, 40, 0), ChunkKey(CRAYON, 40, 0)
    text = TEXTS[copy_1]
    chunks = [indexed(copy_1, text, "6.21.9"), indexed(copy_2, text, "6.21.9")]
    chunks.append(indexed(ChunkKey(OTHER_SUPPLIER, 1, 0), TEXTS[ChunkKey(OTHER_SUPPLIER, 1, 0)]))
    index = pilot_index(chunks)

    result = index.search(unit(1, 0, 0), ["uppsägningsrät"], ALL, 8, 100, 60)

    assert [section.section_hash for section in result].count("6.21.9") == 1
    # The copies tie in both branches: the lower key ("0a5491..." < "185872...").
    assert result[0].key == copy_1
    assert (result[0].vector_rank, result[0].text_rank) == (1, 1)


def test_build_refuses_a_missing_embedding() -> None:
    chunks = [indexed(key, text) for key, text in TEXTS.items()]
    embeddings = embeddings_for(chunks)
    del embeddings[chunks[0].text_hash]

    with pytest.raises(KeyError, match="1 of the plan's texts"):
        OfflineIndex.build(IndexPlan(tuple(chunks), 0), embeddings, SCOPES, {})


def test_build_refuses_embeddings_that_are_not_normalised() -> None:
    chunks = [indexed(key, text) for key, text in TEXTS.items()]
    embeddings = {chunk.text_hash: [1.0, 1.0, 0.0] for chunk in chunks}

    with pytest.raises(ValueError, match="normalised"):
        OfflineIndex.build(IndexPlan(tuple(chunks), 0), embeddings, SCOPES, {})


def test_build_refuses_a_key_twice() -> None:
    key = ChunkKey(CRAYON, 1, 0)
    chunks = (indexed(key, "ett"), indexed(key, "två"))

    with pytest.raises(ValueError, match="twice"):
        OfflineIndex.build(IndexPlan(chunks, 0), embeddings_for(chunks), SCOPES, {})


def test_an_empty_index_finds_nothing() -> None:
    index = OfflineIndex.build(IndexPlan((), 0), {}, {}, {})

    assert index.vector_candidates([1.0, 0.0], ALL, 10) == []
    assert index.text_candidates(["avtal"], ALL, 10) == []
    assert index.search([1.0], ["avtal"], ALL, 8, 100, 60) == []


def test_sparse_weights_by_column() -> None:
    weights = SparseWeights.from_rows([{0: 1.5, 2: 0.25}, {}, {2: 2.0}], term_count=4)

    assert weights.starts.tolist() == [0, 1, 1, 3, 3]
    assert weights.rows.tolist() == [0, 0, 2]
    assert weights.values.tolist() == [1.5, 0.25, 2.0]
    assert weights.scores([2, 0], 3).tolist() == [1.75, 0.0, 2.0]
    assert weights.scores([1, 3], 3).tolist() == [0.0, 0.0, 0.0]


def _key(chunk: IndexedChunk) -> ChunkKey:
    return chunk.key
