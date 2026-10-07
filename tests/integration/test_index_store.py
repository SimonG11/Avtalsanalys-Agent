"""Integration test: step 6's database side (ingestion/index_store.py) against a real Postgres.

What:
    Stores a small pilot-like corpus (`store_corpus`): two agreement pages of
    the 2026-10-05 register, IT-drift Mindre and Bemanningstjänster, and six
    files of real clauses split by step 3, checked by steps 4 and 5, with one
    section held back. `build_index` then runs the `index` command on it with
    a stand-in embedder. The tests check what step 6 reads, the embedding
    cache, the stored index (chunks, embeddings, BM25 weights, words, scopes
    and the build row), that a second build replaces the index and embeds
    nothing new, and that `clear_index`, a new run of step 3 or deleting a
    file empties the index while the cache stays.

Why:
    pgvector's types, the cascades from the chunks and files, and the one
    transaction that replaces the index are SQL that unit tests cannot cover.
    `tests/integration/test_hybrid_search.py` searches the same corpus.

How:
    Uses the `engine` fixture from conftest.py. The clauses are verbatim
    pilot text: IT-drift Mindre's general terms 6.21.2.4 and 6.21.4
    (0a5491b1398e p. 24), printed again as chapter 6 of a procurement
    document; sections 1.10.3-1.10.5 of a main document (0692da436391 p. 14),
    in two suppliers' cards and in Bemanningstjänster's main document; and
    sections 18 and 19 of the draft security agreement (07629941f3e6 pp. 9-10),
    linked from both pages. The embedders give exact unit vectors
    (`TopicEmbedder`: one 1; `SignEmbedder`: sixteen values of ±1/4), so
    pgvector's float32 sums are exact and the rankings can be asserted.
"""

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from unittest.mock import patch

import pytest
from pgvector.sparsevec import SparseVector
from sqlalchemy import Engine, delete, func, select, update

from avtalsagent.db import models
from avtalsagent.db.session import session_factory
from avtalsagent.domain.documents import AgreementPage, DocumentLink
from avtalsagent.domain.extracted import (
    DocumentExtraction,
    DocumentMetadata,
    DocumentType,
    Finding,
    Severity,
)
from avtalsagent.domain.parsed import Block, BlockKind, ParsedDocument
from avtalsagent.domain.register import RegisterRow, RegisterVersion
from avtalsagent.domain.search import ChunkKey, DocumentScope
from avtalsagent.ingestion import __main__ as cli
from avtalsagent.ingestion.catalog import save_fetch
from avtalsagent.ingestion.checks import still_published
from avtalsagent.ingestion.extraction_store import (
    catalog_links,
    register_entries,
    save_extraction,
    save_findings,
)
from avtalsagent.ingestion.index_store import (
    IndexBuildInfo,
    cached_embeddings,
    clear_index,
    current_build,
    files_gone_since_checked,
    index_sources,
    metadata_agreement_numbers,
    metadata_document_types,
    save_cached_embeddings,
    save_index,
)
from avtalsagent.ingestion.section_store import document_links, save_sections
from avtalsagent.ingestion.step1_fetch import FetchResult, FetchStatus, StoredDocument
from avtalsagent.ingestion.step3_chunk import chunk_document, document_context
from avtalsagent.ingestion.step6_index import document_scopes
from avtalsagent.register.load import load_register
from avtalsagent.retrieval import swedish_text
from avtalsagent.retrieval.bm25 import build_term_index, document_weights
from avtalsagent.retrieval.embedder import Embedder

# --- The corpus -------------------------------------------------------------------------------

IT_DRIFT_PAGE = "https://www.avropa.se/ramavtal/ramavtalsomraden/it-drift/it-drift-mindre/"
BEMANNING_PAGE = "https://www.avropa.se/ramavtal/ramavtalsomraden/bemanningstjanster/it-1000/"
IT_DRIFT = "23.3-5890-2023"
BEMANNING = "23.3-14537-2023"
ADVANIA = "23.3-5890-2023-003"
NETBIN = "23.3-5890-2023-001"

TERMS = "a" * 64  # IT-drift Mindre: Allmänna villkor
PROCUREMENT = "b" * 64  # IT-drift Mindre: Upphandlingsdokument, with the terms as chapter 6
ADVANIA_CARD = "c" * 64  # IT-drift Mindre: Advania's card
NETBIN_CARD = "d" * 64  # IT-drift Mindre: NetBin's card
MAIN = "e" * 64  # Bemanningstjänster: Ramavtalets huvuddokument
TEMPLATE = "f" * 64  # both pages: Utkast till Säkerhetsskyddsavtal
FILES = (TERMS, PROCUREMENT, ADVANIA_CARD, NETBIN_CARD, MAIN, TEMPLATE)

# (heading, paragraphs, page)
Clause = tuple[str, tuple[str, ...], int]

TERMINATION: Clause = (
    "6.21.2.4 Fel som utgör väsentligt avtalsbrott",
    (
        "Utgör Felet ett väsentligt avtalsbrott har Avropsberättigad rätt att skriftligen säga "
        "upp Kontraktet enligt avsnitt Avropsberättigads uppsägningsrätt.",
    ),
    24,
)
PENALTY: Clause = (
    "6.21.4 Ansvar och vite vid övriga avtalsbrott",
    (
        "Vitets storlek uppgår till 25 000 SEK och utgår per påbörjad vecka som bristen "
        "kvarstår. Vite ska högst uppgå till 100 000 SEK. Understiger Kontraktets värde "
        "100 000 SEK ska vitet högst uppgå till Kontraktets värde.",
        "Om bristen inte åtgärdas utgår maximalt vite. Om maximalt vite uppnåtts, eller bristen "
        "annars är av väsentlig betydelse för Avropsberättigad, har Avropsberättigad rätt att "
        "säga upp Kontraktet enligt avsnitt Avropsberättigads uppsägningsrätt.",
    ),
    24,
)
FIXED_PRICES: Clause = (
    "1.10.3 Fasta priser i särskild fördelningsnyckel",
    (
        "Ramavtalsleverantörens priser för Konsulttjänster i Avrop genom särskild "
        "fördelningsnyckel anges i bilaga Priser. Priset gäller för Avrop enligt samtliga "
        "villkor fastställda och ska gälla oavsett antal timmar som omfattas av Avropet.",
    ),
    14,
)
PRICE_ADJUSTMENT: Clause = (
    "1.10.4 Prisjustering",
    (
        "Priserna är fasta i ett (1) år från och med att Ramavtalet har trätt i kraft.",
        "Därefter kan antingen Kammarkollegiet eller Ramavtalsleverantören begära prisjustering "
        'en (1) gång per år i enlighet med SCB:s index "Arbetskostnadsindex för tjänstemän, '
        "privat sektor (AKI) efter näringsgren SNI 2007, M Företag inom juridik, ekonomi, "
        'vetenskap och teknik, "preliminär serie"". Som basmånad gäller den månad då ramavtalet '
        "tecknades. Jämförelsemånad vid prisjustering är det senast publicerade indextalet.",
        "Prisjustering gäller för Kontrakt tecknat till följd av Avropsförfrågan som skickas "
        "från och med den första kalenderdagen i månaden efter att Kammarkollegiet skriftligen "
        "bekräftat prisjusteringen.",
        "Prisjustering enligt detta avsnitt omfattar inte redan tecknade Kontrakt. Kontrakt kan "
        "prisjusteras enligt bilaga Allmänna villkor.",
    ),
    14,
)
FACTORING: Clause = (
    "1.10.5 Factoring och fakturahantering",
    (
        "Ramavtalsleverantören har rätt att anlita Underleverantör för factoring eller "
        "fakturahantering vid fakturering enligt Kontrakt endast efter skriftligt godkännande "
        "av Kammarkollegiet. För att Ramavtalsleverantören ska ha rätt till det enligt ett redan "
        "tecknat Kontrakt behövs ett godkännande från Avropsberättigad.",
    ),
    14,
)
SECURITY_END: Clause = (
    "18 Åtgärder när Säkerhetsskyddsavtalet upphör att gälla",
    (
        "När detta Säkerhetsskyddsavtal har upphört ska Verksamhetsutövaren upplysa "
        "Leverantören om den tystnadsplikt som följer av 5 kap. 2 § säkerhetsskyddslagen "
        "(2018:585).",
    ),
    9,
)
SECURITY_TERMINATION: Clause = (
    "19 Ikraftträdande och uppsägning",
    (
        "Detta Säkerhetsskyddsavtal träder i kraft vid det datum båda Parter har undertecknat "
        "det och gäller tills vidare till dess det skriftligen sägs upp av någon av Parterna.",
        "Verksamhetsutövaren kan ensidigt säga upp detta Säkerhetsskyddsavtal liksom Kontraktet "
        "med omedelbar verkan om Leverantören har brutit mot Säkerhetsskyddsavtalet.",
    ),
    10,
)


@dataclass(frozen=True)
class CorpusFile:
    sha256: str
    title: str  # the link text
    pages: tuple[str, ...]
    agreement_number: str | None  # set for a supplier's card
    document_type: DocumentType
    clauses: tuple[Clause, ...]  # sections 0, 1, ... (no text before the first heading)


CORPUS = (
    CorpusFile(
        TERMS,
        "Allmänna villkor",
        (IT_DRIFT_PAGE,),
        None,
        DocumentType.GENERAL_TERMS,
        (TERMINATION, PENALTY),
    ),
    CorpusFile(
        PROCUREMENT,
        "Upphandlingsdokument",
        (IT_DRIFT_PAGE,),
        None,
        DocumentType.PROCUREMENT_DOCUMENT,
        (TERMINATION, PENALTY),
    ),
    CorpusFile(
        ADVANIA_CARD,
        "Ramavtal",
        (IT_DRIFT_PAGE,),
        ADVANIA,
        DocumentType.SUPPLIER_AGREEMENT,
        (PRICE_ADJUSTMENT, FACTORING),
    ),
    CorpusFile(
        NETBIN_CARD,
        "Ramavtal",
        (IT_DRIFT_PAGE,),
        NETBIN,
        DocumentType.SUPPLIER_AGREEMENT,
        (FIXED_PRICES, PRICE_ADJUSTMENT),
    ),
    CorpusFile(
        MAIN,
        "Ramavtalets huvuddokument",
        (BEMANNING_PAGE,),
        None,
        DocumentType.MAIN_DOCUMENT,
        (PRICE_ADJUSTMENT, FACTORING),
    ),
    CorpusFile(
        TEMPLATE,
        "Utkast till Säkerhetsskyddsavtal",
        (IT_DRIFT_PAGE, BEMANNING_PAGE),
        None,
        DocumentType.TEMPLATE,
        (SECURITY_END, SECURITY_TERMINATION),
    ),
)
# Step 5 holds back the procurement document's copy of 6.21.2.4 (its section 0).
HELD = ChunkKey(PROCUREMENT, 0, 0)

REGISTER_VERSION = RegisterVersion(list_date=date(2026, 10, 5), title="Giltiga ramavtal 2026-10-05")


def register_row(
    source_row: int, number: str, supplier: str, org_number: str, area: str, sub_area: str
) -> RegisterRow:
    procurement, _, sequence = number.rpartition("-")
    period = (date(2024, 11, 14), date(2028, 11, 13))
    if area == "Bemanningstjänster":
        period = (date(2025, 4, 3), date(2029, 4, 2))
    return RegisterRow(
        source_row=source_row,
        agreement_number=number,
        procurement_number=procurement,
        sequence=sequence,
        supplier_name=supplier,
        former_supplier_name=None,
        org_number=org_number,
        framework_area=area,
        sub_area_path=(area, sub_area),
        valid_from=period[0],
        valid_to=period[1],
        max_extension_to=None,
    )


# Rows of the 2026-10-05 list (Bemanningstjänster's sub-area shortened to two levels).
REGISTER = [
    register_row(
        149,
        ADVANIA,
        "Advania Sverige AB",
        "556214-9996",
        "IT-drift",
        "IT-drift Mindre, upp till 200 anställda",
    ),
    register_row(
        2426,
        NETBIN,
        "NetBin Sverige AB",
        "556710-0267",
        "IT-drift",
        "IT-drift Mindre, upp till 200 anställda",
    ),
    register_row(
        14,
        "23.3-14537-2023-001",
        "A Hub Group AB",
        "559199-9601",
        "Bemanningstjänster",
        "Bemanningstjänster - IT-tjänster upp till 1000 timmar",
    ),
]


def parsed(file: CorpusFile) -> ParsedDocument:
    blocks: list[Block] = []
    for heading, paragraphs, page in file.clauses:
        blocks.append(Block(kind=BlockKind.HEADING, text=heading, page=page))
        blocks.extend(Block(kind=BlockKind.TEXT, text=text, page=page) for text in paragraphs)
    return ParsedDocument(
        sha256=file.sha256, file_type="pdf", parser="test", pages=(), blocks=tuple(blocks)
    )


def metadata(file: CorpusFile) -> DocumentMetadata:
    return DocumentMetadata(
        sha256=file.sha256,
        title=file.title,
        document_type=file.document_type,
        type_rule="R05",
        agreement_number=file.agreement_number,
        annex_number=None,
        first_chapter=6 if file.sha256 == PROCUREMENT else None,
        tendsign_cover=None,
        is_template=file.document_type is DocumentType.TEMPLATE,
        version_date=None,
        version_rule=None,
        published_on=None,
        site_updated=None,
    )


def link(file: CorpusFile) -> DocumentLink:
    return DocumentLink(
        url=f"https://www.avropa.se/globalassets/{file.sha256[:8]}.pdf",
        version="v1",
        title=file.title,
        category=None if file.agreement_number else "Avtal",
        agreement_number=file.agreement_number,
        file_type="pdf",
        site_updated=None,
    )


def store_corpus(engine: Engine) -> None:
    """The corpus through steps 1-5, as `fetch` and `process` store it; no index yet."""
    pages = [
        AgreementPage(
            url=url,
            title=title,
            procurement_numbers=(procurement,),
            agreement_period=None,
            documents=tuple(link(file) for file in CORPUS if url in file.pages),
        )
        for url, title, procurement in (
            (IT_DRIFT_PAGE, "IT-drift Mindre, upp till 200 anställda", IT_DRIFT),
            (BEMANNING_PAGE, "Bemanningstjänster - IT-tjänster upp till 1000 timmar", BEMANNING),
        )
    ]
    results = [
        FetchResult(
            link(file),
            FetchStatus.NEW,
            StoredDocument(link(file).url, "v1", file.sha256, 1, f"documents/{file.sha256}.pdf"),
        )
        for file in CORPUS
    ]
    factory = session_factory(engine)
    with factory.begin() as session:
        clear_index(session)
        session.execute(delete(models.EmbeddingCache))
        session.execute(delete(models.ParsedFile))
        session.execute(delete(models.ValidationFinding))
        for model in (models.AgreementPageDocument, models.SourceDocument, models.AgreementPage):
            session.execute(delete(model))
        load_register(session, REGISTER_VERSION, REGISTER)
        save_fetch(session, pages, results, [page.url for page in pages])
    with factory() as session:
        contexts = {sha: document_context(info) for sha, info in document_links(session).items()}
    documents = [parsed(file) for file in CORPUS]
    held = Finding(
        check="missing_text",
        severity=Severity.QUARANTINE,
        subject="6.21.2.4",
        message="Avsnitt 6.21.2.4 ligger på en skannad sida utan text.",
        sha256=PROCUREMENT,
        section=HELD.section_position,
    )
    with factory.begin() as session:
        save_sections(
            session, documents, [chunk_document(d, contexts[d.sha256]) for d in documents]
        )
        save_extraction(
            session,
            [DocumentExtraction(metadata=metadata(f), facts=(), mentions=()) for f in CORPUS],
            [],
        )
        save_findings(session, [held])


class TopicEmbedder:
    """Stands in for the embedding model: the vector of the first topic the text names.

    Each vector has a single 1, so every inner product is exactly 1 or 0, in
    pgvector as in numpy: a question about penalties ranks the chunks that
    name "vite" first, in key order, then the others in key order.
    """

    TOPICS = ("vite", "prisjuster", "factoring", "uppsäg", "upphör")
    name = "topics:6"

    def __init__(self) -> None:
        self.texts: list[str] = []  # every text embedded as a document

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        self.texts.extend(texts)
        return [self.embed_query(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        lowered = text.lower()
        topic = next((n for n, word in enumerate(self.TOPICS) if word in lowered), 5)
        return [1.0 if n == topic else 0.0 for n in range(len(self.TOPICS) + 1)]


class SignEmbedder:
    """Stands in for the embedding model: ±1/4 in 16 dimensions from the text's sha256.

    The vectors have length 1, and every product and sum of their values is a
    multiple of 1/16, exact in float32 in any order: pgvector's and numpy's
    scores are equal bit for bit, ties included, so rankings can be compared.
    """

    name = "signs:16"

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self.embed_query(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return [0.25 if digest[n] % 2 else -0.25 for n in range(16)]


def build_index(engine: Engine, embedder: Embedder) -> None:
    """Step 6 as the `index` command runs it, on the test database."""
    with patch.object(cli, "create_db_engine", lambda: engine):
        cli.index(embedder)


# --- Tests ------------------------------------------------------------------------------------


@pytest.fixture
def corpus(engine: Engine) -> Engine:
    store_corpus(engine)
    return engine


@pytest.fixture
def indexed(corpus: Engine) -> Engine:
    build_index(corpus, TopicEmbedder())
    return corpus


def count(engine: Engine, model: type[models.Base]) -> int:
    with session_factory(engine)() as session:
        return session.scalar(select(func.count()).select_from(model)) or 0


def test_step_6_reads_every_chunk_with_its_section_and_the_cards_numbers(corpus: Engine) -> None:
    with session_factory(corpus)() as session:
        sources = index_sources(session)
        numbers = metadata_agreement_numbers(session)
        section_texts = dict(
            session.execute(
                select(models.DocumentSection.sha256, models.DocumentSection.text).where(
                    models.DocumentSection.position == 1
                )
            ).all()
        )

    assert [source.key for source in sources] == [
        ChunkKey(sha256, section, 0) for sha256 in FILES for section in (0, 1)
    ]
    penalty = next(source for source in sources if source.key == ChunkKey(TERMS, 1, 0))
    assert penalty.section_text == section_texts[TERMS]
    assert penalty.section_text.startswith("6.21.4 Ansvar och vite vid övriga avtalsbrott\n\n")
    assert penalty.context_header == (
        "IT-drift (23.3-5890-2023) › Allmänna villkor › 6.21.4 Ansvar och vite vid övriga "
        "avtalsbrott"
    )
    assert numbers == {sha256: None for sha256 in FILES} | {
        ADVANIA_CARD: ADVANIA,
        NETBIN_CARD: NETBIN,
    }


def test_the_embedding_cache_keeps_the_first_vector_per_model_and_text(corpus: Engine) -> None:
    factory = session_factory(corpus)
    # More hashes than one lookup query takes (_CACHE_LOOKUP_BATCH).
    first = {f"{n:064x}": [0.5, -0.25, 0.125, 1.0] for n in range(2_500)}
    with factory.begin() as session:
        save_cached_embeddings(session, "text-embedding-3-large:4", first)
    with factory.begin() as session:
        again = {f"{0:064x}": [1.0, 0.0, 0.0, 0.0]}
        save_cached_embeddings(session, "text-embedding-3-large:4", again)
    with factory() as session:
        found = cached_embeddings(session, "text-embedding-3-large:4", [*first, "f" * 64])
        other_model = cached_embeddings(session, "text-embedding-3-large:8", first)

    assert found == first  # exact: the values are exact in float32
    assert other_model == {}


def test_the_index_holds_every_chunk_the_quarantine_lets_through(indexed: Engine) -> None:
    with session_factory(indexed)() as session:
        stored = session.scalars(select(models.SearchChunk)).all()
        build = current_build(session)
        terms = {
            row.term: (row.id, row.chunk_count)
            for row in session.scalars(select(models.SearchTerm))
        }
        plan, scopes = cli.index_input(session)

    keys = {ChunkKey(row.sha256, row.section_position, row.position) for row in stored}
    assert keys == {ChunkKey(sha, section, 0) for sha in FILES for section in (0, 1)} - {HELD}
    assert build is not None
    assert build == IndexBuildInfo(
        embedding_model="topics:6",
        analyser=swedish_text.ANALYSER,
        term_count=len(terms),
        chunk_count=11,
        held_back_count=1,
        document_count=6,
        built_at=build.built_at,
    )
    # The words are numbered 0, 1, ... in sorted order: the positions in term_weights.
    term_index = build_term_index([chunk.terms for chunk in plan.chunks])
    assert terms == {
        term: (number, term_index.chunk_counts[term]) for term, number in term_index.ids.items()
    }

    by_key = {ChunkKey(row.sha256, row.section_position, row.position): row for row in stored}
    for chunk in plan.chunks:
        row = by_key[chunk.key]
        weights = document_weights(term_index, chunk.terms)
        assert row.section_hash == chunk.section_hash
        assert row.embedding == TopicEmbedder().embed_query(chunk.embedded_text)
        assert isinstance(row.term_weights, SparseVector)
        assert row.term_weights.dimensions() == len(terms)
        assert row.term_weights.indices() == list(weights)
        assert row.term_weights.values() == pytest.approx(list(weights.values()), rel=1e-6)


def test_each_indexed_file_has_its_scope(indexed: Engine) -> None:
    with session_factory(indexed)() as session:
        stored = {
            row.sha256: DocumentScope(
                row.sha256,
                tuple(row.framework_areas),
                tuple(row.procurement_numbers),
                tuple(row.agreement_numbers),
                tuple(row.page_titles),
                row.document_type,
            )
            for row in session.scalars(select(models.DocumentScope))
        }
        expected = document_scopes(
            catalog_links(session),
            register_entries(session),
            metadata_agreement_numbers(session),
            metadata_document_types(session),
        )

    assert stored == expected
    assert stored[TEMPLATE] == DocumentScope(
        TEMPLATE,
        ("Bemanningstjänster", "IT-drift"),
        (BEMANNING, IT_DRIFT),
        (),
        (
            "Bemanningstjänster - IT-tjänster upp till 1000 timmar",
            "IT-drift Mindre, upp till 200 anställda",
        ),
        "template",
    )
    assert stored[ADVANIA_CARD].agreement_numbers == (ADVANIA,)
    assert stored[TERMS].document_type == "general_terms"


def test_files_whose_pages_all_went_after_process_stop_the_index(corpus: Engine) -> None:
    factory = session_factory(corpus)
    with factory.begin() as session:
        session.execute(
            update(models.AgreementPage)
            .where(models.AgreementPage.url == IT_DRIFT_PAGE)
            .values(missing_since=datetime(2026, 10, 7, tzinfo=UTC))
        )
    with factory() as session:
        gone = files_gone_since_checked(session)
    # The template is still linked from the Bemanningstjänster page.
    assert gone == {TERMS, PROCUREMENT, ADVANIA_CARD, NETBIN_CARD}

    with pytest.raises(cli.CommandError, match="4 files lost their last agreement page"):
        build_index(corpus, TopicEmbedder())
    assert count(corpus, models.IndexBuild) == 0

    # Once step 5 has judged a file (held back, or the finding accepted), it is not asked again.
    judged = Finding(
        check=still_published.CHECK,
        severity=Severity.QUARANTINE,
        subject=IT_DRIFT_PAGE,
        message="Ingen avtalssida på avropa.se länkar längre till filen.",
        sha256=TERMS,
    )
    with factory.begin() as session:
        save_findings(session, [judged])
    with factory() as session:
        assert files_gone_since_checked(session) == {PROCUREMENT, ADVANIA_CARD, NETBIN_CARD}


def test_a_second_build_replaces_the_index_and_embeds_nothing_new(indexed: Engine) -> None:
    before = {model: count(indexed, model) for model in (models.SearchChunk, models.SearchTerm)}
    embedder = TopicEmbedder()

    build_index(indexed, embedder)

    assert embedder.texts == []  # every text was in the cache
    assert {model: count(indexed, model) for model in before} == before
    assert count(indexed, models.IndexBuild) == 1
    assert count(indexed, models.DocumentScope) == 6


def test_clear_index_empties_the_index_but_keeps_the_cache(indexed: Engine) -> None:
    with session_factory(indexed).begin() as session:
        clear_index(session)
    with session_factory(indexed)() as session:
        build = current_build(session)

    assert build is None
    for model in (models.SearchChunk, models.SearchTerm, models.DocumentScope):
        assert count(indexed, model) == 0
    assert count(indexed, models.EmbeddingCache) == 11


def test_a_deleted_file_takes_its_index_rows_with_it(indexed: Engine) -> None:
    with session_factory(indexed).begin() as session:
        session.execute(delete(models.ParsedFile).where(models.ParsedFile.sha256 == TERMS))
    with session_factory(indexed)() as session:
        files = set(session.scalars(select(models.SearchChunk.sha256)))
        scoped = set(session.scalars(select(models.DocumentScope.sha256)))

    assert files == scoped == set(FILES) - {TERMS}


def test_step_3_alone_empties_the_chunks_and_scopes_of_the_index(indexed: Engine) -> None:
    # `save_sections` replaces every parsed_file row: the index's rows go with them (fail
    # closed), also if a caller forgot `clear_index`.
    documents = [parsed(file) for file in CORPUS]
    with session_factory(indexed)() as session:
        contexts = {sha: document_context(info) for sha, info in document_links(session).items()}
    with session_factory(indexed).begin() as session:
        save_sections(
            session, documents, [chunk_document(d, contexts[d.sha256]) for d in documents]
        )

    assert count(indexed, models.SearchChunk) == 0
    assert count(indexed, models.DocumentScope) == 0


def test_an_index_without_an_embedding_or_a_scope_is_refused_and_the_old_one_kept(
    indexed: Engine,
) -> None:
    factory = session_factory(indexed)
    with factory() as session:
        plan, scopes = cli.index_input(session)
        embeddings = cached_embeddings(session, "topics:6", {c.text_hash for c in plan.chunks})
    terms = build_term_index([chunk.terms for chunk in plan.chunks])
    first = plan.chunks[0]

    with (
        pytest.raises(ValueError, match="1 chunks have no embedding"),
        factory.begin() as session,
    ):
        partial = {h: v for h, v in embeddings.items() if h != first.text_hash}
        save_index(session, plan, terms, partial, scopes, "topics:6", swedish_text.ANALYSER)
    with (
        pytest.raises(ValueError, match="1 indexed files have no scope"),
        factory.begin() as session,
    ):
        unscoped = {sha: scope for sha, scope in scopes.items() if sha != TERMS}
        save_index(session, plan, terms, embeddings, unscoped, "topics:6", swedish_text.ANALYSER)

    assert count(indexed, models.SearchChunk) == 11
    assert count(indexed, models.IndexBuild) == 1
