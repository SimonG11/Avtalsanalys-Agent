"""Integration test: the hybrid search (retrieval/hybrid_search.py) against a real Postgres.

What:
    Searches the corpus of `test_index_store.py` (two agreement pages, six
    files of real clauses, one section held back), indexed by the `index`
    command with a stand-in embedder. Checks that a question finds its
    section once with its copies, that BM25 finds a word the embedding
    misses, which files each filter keeps (an agreement: its card and its
    procurement's shared files), that copies are listed only within the
    filters and never when held back, that the search refuses an index
    built with another model or analyser, and that both branches return
    exactly the chunks of the offline search (`evals/offline.py`) for
    several questions and filters (parity).

Why:
    The offline measurement is only worth its numbers if it ranks and filters
    as the SQL does; the filters, `<#>` on `vector` and `sparsevec`, and the
    tie-breaks are SQL that unit tests cannot cover.

How:
    Uses the `engine` fixture from conftest.py and the corpus and embedders
    of `test_index_store.py`. `TopicEmbedder` gives readable rankings
    (questions about "vite" find the chunks that name it); `SignEmbedder`
    gives varied ones for the parity test. Both have exact float32 scores, so
    pgvector and numpy agree to the bit and ties go to the lower chunk key.
"""

import pytest
from sqlalchemy import Engine, update

from avtalsagent.db import models
from avtalsagent.db.session import session_factory
from avtalsagent.domain.search import ChunkKey, SearchFilters
from avtalsagent.ingestion import __main__ as cli
from avtalsagent.ingestion.extraction_store import register_entries
from avtalsagent.ingestion.index_store import cached_embeddings, clear_index
from avtalsagent.ingestion.step6_index import agreement_procurements
from avtalsagent.retrieval import swedish_text
from avtalsagent.retrieval.hybrid_search import (
    IndexNotReadyError,
    SectionCopy,
    search,
    text_candidates,
    vector_candidates,
)
from evals.offline import OfflineIndex
from tests.integration.test_index_store import (
    ADVANIA,
    ADVANIA_CARD,
    BEMANNING,
    FILES,
    HELD,
    IT_DRIFT,
    MAIN,
    NETBIN,
    NETBIN_CARD,
    PROCUREMENT,
    TEMPLATE,
    TERMS,
    SignEmbedder,
    TopicEmbedder,
    build_index,
    store_corpus,
)

A_HUB = "23.3-14537-2023-001"  # Bemanningstjänster; no card of its own in the corpus
UNKNOWN = "23.3-9999-2023-001"


@pytest.fixture
def topics(engine: Engine) -> Engine:
    store_corpus(engine)
    build_index(engine, TopicEmbedder())
    return engine


def run_search(engine: Engine, question: str, limit: int) -> list[tuple[str, int]]:
    with session_factory(engine)() as session:
        hits = search(session, TopicEmbedder(), question, limit=limit)
    return [(hit.sha256, hit.section_position) for hit in hits]


def test_a_question_finds_its_section_once_with_its_copies(topics: Engine) -> None:
    with session_factory(topics)() as session:
        hits = search(session, TopicEmbedder(), "Hur stort är vitet vid avtalsbrott?", limit=3)

    penalty, termination, _ = hits
    assert (penalty.sha256, penalty.section_position) == (TERMS, 1)
    assert (penalty.section_number, penalty.section_title) == (
        "6.21.4",
        "Ansvar och vite vid övriga avtalsbrott",
    )
    assert penalty.path == ("6.21.4 Ansvar och vite vid övriga avtalsbrott",)
    assert (penalty.page_start, penalty.page_end) == (24, 24)
    assert (penalty.file_title, penalty.document_type) == ("Allmänna villkor", "general_terms")
    assert penalty.framework_areas == ("IT-drift",)
    assert penalty.page_titles == ("IT-drift Mindre, upp till 200 anställda",)
    assert penalty.agreement_numbers == ()
    assert penalty.snippet.startswith(
        "6.21.4 Ansvar och vite vid övriga avtalsbrott\n\nVitets storlek uppgår till 25 000 SEK"
    )
    # First in both branches; the procurement document's identical 6.21.4 is the same result.
    assert (penalty.vector_rank, penalty.text_rank) == (1, 1)
    assert penalty.score == pytest.approx(2 / 61)
    assert penalty.copies == (SectionCopy(PROCUREMENT, 1, "6.21.4", "Upphandlingsdokument"),)
    # 6.21.2.4 is also in the procurement document, but that copy is held back: not listed.
    assert (termination.sha256, termination.section_number, termination.copies) == (
        TERMS,
        "6.21.2.4",
        (),
    )
    assert [hit.score for hit in hits] == sorted((hit.score for hit in hits), reverse=True)


def test_bm25_finds_the_words_the_embedding_misses(topics: Engine) -> None:
    # The stand-in embedding knows no topic of the question; its three words are in one
    # section only.
    with session_factory(topics)() as session:
        [first, *_] = search(
            session, TopicEmbedder(), "Vilken tystnadsplikt följer av säkerhetsskyddslagen?"
        )

    assert (first.sha256, first.section_number) == (TEMPLATE, "18")
    assert first.text_rank == 1
    assert first.vector_rank != 1
    assert first.framework_areas == ("Bemanningstjänster", "IT-drift")


@pytest.mark.parametrize(
    ("filters", "files"),
    [
        (SearchFilters(), set(FILES)),
        (
            SearchFilters(framework_area="IT-drift"),
            {TERMS, PROCUREMENT, ADVANIA_CARD, NETBIN_CARD, TEMPLATE},
        ),
        (SearchFilters(framework_area="Bemanningstjänster"), {MAIN, TEMPLATE}),
        (SearchFilters(procurement_number=BEMANNING), {MAIN, TEMPLATE}),
        (
            SearchFilters(procurement_number=IT_DRIFT),
            {TERMS, PROCUREMENT, ADVANIA_CARD, NETBIN_CARD, TEMPLATE},
        ),
        # The card and the procurement's shared files; not NetBin's card.
        (SearchFilters(agreement_number=ADVANIA), {TERMS, PROCUREMENT, ADVANIA_CARD, TEMPLATE}),
        (SearchFilters(agreement_number=NETBIN), {TERMS, PROCUREMENT, NETBIN_CARD, TEMPLATE}),
        (SearchFilters(agreement_number=A_HUB), {MAIN, TEMPLATE}),
        (SearchFilters(agreement_number=UNKNOWN), set()),
        # The template is in both areas and shared in both procurements.
        (SearchFilters(framework_area="Bemanningstjänster", agreement_number=ADVANIA), {TEMPLATE}),
        (SearchFilters(document_type="general_terms"), {TERMS}),
        (SearchFilters(document_type="supplier_agreement"), {ADVANIA_CARD, NETBIN_CARD}),
        (
            SearchFilters(agreement_number=ADVANIA, document_type="supplier_agreement"),
            {ADVANIA_CARD},
        ),
        (SearchFilters(document_type="amendment"), set()),
    ],
)
def test_the_filters_keep_the_files_of_their_scope(
    topics: Engine, filters: SearchFilters, files: set[str]
) -> None:
    with session_factory(topics)() as session:
        vector = vector_candidates(session, TopicEmbedder().embed_query("vite"), filters, 100)
        text = text_candidates(session, swedish_text.query_terms("ramavtal kontrakt"), filters, 100)

    assert {chunk.key.sha256 for chunk in vector} == files
    assert {chunk.key.sha256 for chunk in text} <= files


@pytest.mark.parametrize(
    ("filters", "shown", "copies"),
    [
        (SearchFilters(), (ADVANIA_CARD, 0), [(NETBIN_CARD, 1), (MAIN, 0)]),
        (SearchFilters(framework_area="IT-drift"), (ADVANIA_CARD, 0), [(NETBIN_CARD, 1)]),
        (SearchFilters(agreement_number=ADVANIA), (ADVANIA_CARD, 0), []),
        (SearchFilters(agreement_number=NETBIN), (NETBIN_CARD, 1), []),
        (SearchFilters(framework_area="Bemanningstjänster"), (MAIN, 0), []),
    ],
)
def test_copies_are_listed_within_the_filters_only(
    topics: Engine, filters: SearchFilters, shown: tuple[str, int], copies: list[tuple[str, int]]
) -> None:
    # Section 1.10.4 is in both IT-drift cards and in Bemanningstjänster's main document.
    with session_factory(topics)() as session:
        [first, *_] = search(session, TopicEmbedder(), "Vad gäller vid prisjustering?", filters)

    assert (first.sha256, first.section_position, first.section_number) == (*shown, "1.10.4")
    assert [(copy.sha256, copy.section_position) for copy in first.copies] == copies
    assert {copy.section_number for copy in first.copies} <= {"1.10.4"}


def test_a_held_back_section_is_never_a_candidate(topics: Engine) -> None:
    question = "Fel som utgör väsentligt avtalsbrott: säga upp Kontraktet"
    with session_factory(topics)() as session:
        vector = vector_candidates(
            session, TopicEmbedder().embed_query(question), SearchFilters(), 100
        )
        text = text_candidates(session, swedish_text.query_terms(question), SearchFilters(), 100)

    assert len(vector) == 11
    assert HELD not in {chunk.key for chunk in vector + text}
    assert ChunkKey(TERMS, 0, 0) in {chunk.key for chunk in text}


def test_a_question_without_indexed_words_has_no_bm25_candidates(topics: Engine) -> None:
    with session_factory(topics)() as session:
        unknown = text_candidates(session, ["fotbollsplan"], SearchFilters(), 10)
        nothing = text_candidates(session, [], SearchFilters(), 10)

    assert unknown == nothing == []


def test_the_limit_and_the_candidates_bound_the_results(topics: Engine) -> None:
    with session_factory(topics)() as session:
        vector = vector_candidates(session, TopicEmbedder().embed_query("vite"), SearchFilters(), 3)

    assert [chunk.key for chunk in vector] == [
        ChunkKey(TERMS, 1, 0),  # the two chunks that name "vite", in key order
        ChunkKey(PROCUREMENT, 1, 0),
        ChunkKey(TERMS, 0, 0),  # then every other chunk, in key order
    ]
    assert len(run_search(topics, "Hur stort är vitet?", limit=2)) == 2


class RefusingEmbedder(TopicEmbedder):
    """An embedder of another model, which must not be asked: the check comes first."""

    name = "text-embedding-3-large:1536"

    def embed_query(self, text: str) -> list[float]:
        raise AssertionError("the question was embedded before the index was checked")


def test_the_search_refuses_an_index_of_another_model(topics: Engine) -> None:
    with (
        session_factory(topics)() as session,
        pytest.raises(
            IndexNotReadyError,
            match="embedding model topics:6, not text-embedding-3-large:1536: build it again",
        ),
    ):
        search(session, RefusingEmbedder(), "Hur stort är vitet?")


def test_the_search_refuses_an_index_of_another_analyser(topics: Engine) -> None:
    with session_factory(topics).begin() as session:
        session.execute(update(models.IndexBuild).values(analyser="sv-0 snowballstemmer 2.2.0"))

    with (
        session_factory(topics)() as session,
        pytest.raises(IndexNotReadyError, match="text analyser sv-0 snowballstemmer 2.2.0, not"),
    ):
        search(session, TopicEmbedder(), "Hur stort är vitet?")


def test_the_search_refuses_an_empty_index(topics: Engine) -> None:
    # As after `process`: the sections were replaced and the index emptied.
    with session_factory(topics).begin() as session:
        clear_index(session)

    with (
        session_factory(topics)() as session,
        pytest.raises(IndexNotReadyError, match="there is no search index: build it with"),
    ):
        search(session, TopicEmbedder(), "Hur stort är vitet?")


# --- Parity with the offline search ------------------------------------------------------------

QUESTIONS = (
    "Hur stort är vitet vid avtalsbrott?",
    "Vad gäller vid prisjustering enligt index?",
    "Får leverantören anlita underleverantör för factoring?",
    "När kan Avropsberättigad säga upp Kontraktet?",
    "Vilken tystnadsplikt gäller när Säkerhetsskyddsavtalet har upphört?",
    "Har ramavtal 23.3-5890-2023-003 fasta priser i särskild fördelningsnyckel?",
)
FILTERS = (
    SearchFilters(),
    SearchFilters(framework_area="IT-drift"),
    SearchFilters(framework_area="Bemanningstjänster"),
    SearchFilters(procurement_number=IT_DRIFT),
    SearchFilters(agreement_number=ADVANIA),
    SearchFilters(agreement_number=NETBIN),
    SearchFilters(agreement_number=A_HUB),
    SearchFilters(agreement_number=UNKNOWN),
    SearchFilters(document_type="supplier_agreement"),
    SearchFilters(framework_area="IT-drift", document_type="general_terms"),
)


def offline_index(engine: Engine, model: str) -> OfflineIndex:
    """The offline index of what step 6 stored, from the same plan, cache and scopes."""
    with session_factory(engine)() as session:
        plan, scopes = cli.index_input(session)
        embeddings = cached_embeddings(session, model, {chunk.text_hash for chunk in plan.chunks})
        procurements = agreement_procurements(register_entries(session))
    return OfflineIndex.build(plan, embeddings, scopes, procurements)


@pytest.mark.parametrize("candidates", [100, 3])
def test_both_branches_rank_and_filter_exactly_as_the_offline_search(
    engine: Engine, candidates: int
) -> None:
    store_corpus(engine)
    embedder = SignEmbedder()
    build_index(engine, embedder)
    offline = offline_index(engine, embedder.name)

    with session_factory(engine)() as session:
        for question in QUESTIONS:
            vector = embedder.embed_query(question)
            terms = swedish_text.query_terms(question)
            for filters in FILTERS:
                case = f"{question!r} {filters}"
                in_sql = vector_candidates(session, vector, filters, candidates)
                assert in_sql == offline.vector_candidates(vector, filters, candidates), case
                in_sql = text_candidates(session, terms, filters, candidates)
                assert in_sql == offline.text_candidates(terms, filters, candidates), case

                hits = search(session, embedder, question, filters, 5, candidates, 60)
                fused = offline.search(vector, terms, filters, 5, candidates, 60)
                assert [
                    (hit.sha256, hit.section_position, hit.vector_rank, hit.text_rank, hit.score)
                    for hit in hits
                ] == [
                    (f.key.sha256, f.key.section_position, f.vector_rank, f.text_rank, f.score)
                    for f in fused
                ], case
