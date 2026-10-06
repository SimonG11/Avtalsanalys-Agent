"""Tests for avtalsagent.ingestion.step4_extract.

The lines are real, from the M4 pilot files (avropa.se and the register of
2026-10-05), cited as sha[:12] and page (p) or section (§):
- 14aa1cc8ee3d, the supplier "Ramavtal" of 23.3-2940-20:026 (Experis AB): p1
  "IT-konsulttjänster 2020 Dnr 23.3-2940-20 Ramavtal 23.3.2940-20:026 Experis AB",
  the headings §9.15 and §9.18, and the sentences of §9.18.2 and §9.18.5;
- 185872a6bb90, the supplier "Ramavtal" of 23.3-2649-2022-003 (Crayon AB): its page
  header, §1.2, §1.3.1 and the e-signature certificate on p18, whose signers'
  names and ids are made up;
- 0692da436391, the main document of IT-konsulttjänster 1: its TendSign cover on
  p1, the party clause on p4 with the supplier slot empty, §1.7 on p9 with
  "[DATUM (dag-mån-år)]";
- 93bd3b2bfeae, "Utkast till Säkerhetsskyddsavtal (Nivå 1)", linked from 15 pages
  under three spellings (13, 1 and 1), p1 "[LEVERANTÖR], organisationsnummer
  XXXXXX-XXXX";
- b96dcfa40767, "Utkast till datadelningsavtal", "Senast uppdaterad" 2023-02-14 on
  Informationsförsörjning and 2026-03-02 on Licenser och licenstjänster;
- 54211e718d8e §1.4 "(dnr 23.3-7067-17)", a procurement the register does not have.
The page URLs are shortened. Made up, since no pilot file or register row has them:
a card link that writes its number "23.3.2649-22-003" and a register row that writes
Microsoft's agreement "23.5-3718-24".
"""

from collections.abc import Sequence
from datetime import date

from avtalsagent.domain.documents import CatalogLink
from avtalsagent.domain.extracted import (
    DocumentType,
    Fact,
    FactKind,
    FactRole,
    ReferenceStatus,
)
from avtalsagent.domain.parsed import Block, BlockKind, ParsedDocument
from avtalsagent.domain.register import RegisterEntry
from avtalsagent.ingestion.extract.reference_resolver import resolution_rate
from avtalsagent.ingestion.extract.title_matcher import MatchStats
from avtalsagent.ingestion.step3_chunk import DocumentContext, chunk_document
from avtalsagent.ingestion.step4_extract import (
    ExtractionInput,
    RegisterSpelling,
    extract_corpus,
    extract_document,
)

PAGE = "https://www.avropa.se/ramavtal/ramavtalsomraden/"
FILES = "https://www.avropa.se/globalassets/bilagor/1.-aktuella-rao/"
CARD_026 = "14aa1cc8ee3d99206f9eb6731234934a52958729579bad511f2080f7b4f53611"
CARD_CRAYON = "185872a6bb906b04efb21779323d6d30e0f2731c2cf7ce2e0d1882b8a055a7c6"
MAIN_ITK1 = "0692da436391917c01ad4fe2bc651d7d4faa3ea2ff400ce6960d2e3206306164"
SECURITY_1 = "93bd3b2bfeae3e5dc34d0c0e2ad086ef078ca88d06ec21c90e8a87cf71862e27"
DATA_SHARING = "b96dcfa407672b0c60bf4b0e4a15eab54414b3ecc76fca868596c20ec098b11c"


def entry(
    agreement: str, procurement: str, supplier: str, org_number: str, sub_area: str
) -> RegisterEntry:
    return RegisterEntry(
        agreement_number=agreement,
        procurement_number=procurement,
        org_number=org_number,
        supplier_name=supplier,
        former_supplier_name=None,
        framework_area="IT-konsulttjänster Resurskonsulter",
        sub_area_path=("IT-konsulttjänster Resurskonsulter", sub_area),
        valid_from=date(2022, 12, 1),
        valid_to=date(2026, 11, 30),
        max_extension_to=None,
    )


EXPERIS = entry(
    "23.3-2940-20:026",
    "23.3-2940-20",
    "Experis AB",
    "556855-1104",
    "IT-konsulttjänster 4. Arkitektur och utveckling",
)
CRAYON = entry(
    "23.3-2649-2022-003",
    "23.3-2649-2022",
    "Crayon AB",
    "556635-9799",
    "Informationsförsörjning",
)
MICROSOFT = entry(
    "23.5-3718-2024", "23.5-3718-2024", "Microsoft AB", "556233-4804", "Volymavtal för Microsoft"
)
REGISTER = RegisterSpelling.of([EXPERIS, CRAYON, MICROSOFT])


def link(
    sha256: str,
    title: str,
    file_name: str = "dokument.pdf",
    *,
    category: str | None = "Avtal",
    agreement_number: str | None = None,
    site_updated: date | None = date(2026, 5, 8),
    page: str = "it-konsulttjanster-1.-verksamhetens-it-behov",
) -> CatalogLink:
    return CatalogLink(
        sha256=sha256,
        url=FILES + file_name,
        title=title,
        category=category,
        agreement_number=agreement_number,
        site_updated=site_updated,
        page_url=PAGE + page,
        page_title=page,
        page_procurement_numbers=("23.3-1688-2024",),
        page_period=None,
    )


def block(text: str, page: int = 1, kind: BlockKind = BlockKind.TEXT) -> Block:
    return Block(kind=kind, text=text, page=page)


def heading(text: str, page: int = 1) -> Block:
    return block(text, page, BlockKind.HEADING)


def document(sha256: str, *blocks: Block) -> ParsedDocument:
    return ParsedDocument(
        sha256=sha256, file_type="pdf", parser="test", pages=(), blocks=tuple(blocks)
    )


def extract(parsed: ParsedDocument, links: Sequence[CatalogLink]) -> ExtractionInput:
    """The file as step 4 gets it: parsed, split by step 3, with its links."""
    context = DocumentContext(agreement="IT-konsulttjänster", document=links[0].title)
    return ExtractionInput(parsed, chunk_document(parsed, context), tuple(links))


def fact(kind: FactKind, value: str, raw: str) -> Fact:
    return Fact(kind=kind, value=value, raw=raw, rule="PROC", block=0, page=1, role=FactRole.SELF)


# --- The register's spelling ------------------------------------------------------------------


class TestRegisterSpelling:
    def test_a_number_in_another_spelling_is_stored_as_the_register_writes_it(self) -> None:
        # 14aa1cc8ee3d p1 writes its agreement number with a dot after "23.3" and its
        # procurement as the register does; both are found as their keys.
        own = fact(FactKind.AGREEMENT_NUMBER, "23.3-2940-2020-026", "23.3.2940-20:026")
        case = fact(FactKind.PROCUREMENT_NUMBER, "23.3-2940-2020", "23.3-2940-20")

        assert REGISTER.respell(own).value == "23.3-2940-20:026"
        assert REGISTER.respell(case).value == "23.3-2940-20"
        assert REGISTER.respell(own).raw == "23.3.2940-20:026"  # the text stays as written

    def test_a_number_the_register_lacks_keeps_its_key(self) -> None:
        cited = fact(FactKind.PROCUREMENT_NUMBER, "23.3-7067-2017", "23.3-7067-17")

        assert REGISTER.respell(cited).value == "23.3-7067-2017"

    def test_only_case_and_agreement_numbers_are_respelled(self) -> None:
        # A placeholder's value names the field; "23.3-2940-2020" here is made up to
        # show that a value is only looked up for the two kinds of number.
        placeholder = fact(FactKind.PLACEHOLDER, "23.3-2940-2020", "23.3-2940-20:[XXX]")

        assert REGISTER.respell(placeholder) == placeholder

    def test_an_agreement_number_without_a_sequence_is_its_procurement(self) -> None:
        # Microsoft's volume agreement has one number for both; it keys once.
        assert RegisterSpelling.of([MICROSOFT]).numbers == {"23.5-3718-2024": "23.5-3718-2024"}
        number = fact(FactKind.AGREEMENT_NUMBER, "23.5-3718-2024", "Dnr 23.5-3718-2024")
        assert REGISTER.respell(number).value == "23.5-3718-2024"

    def test_the_procurement_spelling_wins_for_a_shared_key(self) -> None:
        # Made up: no register row of the pilot spells such an agreement number
        # differently from its procurement, but if one did, the procurement's
        # spelling would be used for both.
        odd = MICROSOFT.model_copy(update={"agreement_number": "23.5-3718-24"})

        assert RegisterSpelling.of([odd]).numbers == {"23.5-3718-2024": "23.5-3718-2024"}

    def test_a_cards_agreement_number_from_its_link(self) -> None:
        assert REGISTER.agreement_number("23.3.2940-20:026") == "23.3-2940-20:026"
        assert REGISTER.agreement_number("23.3-2940-20:099") == "23.3-2940-20:099"  # unknown
        assert REGISTER.agreement_number("Ramavtal") == "Ramavtal"  # not a number


# --- One file ---------------------------------------------------------------------------------

# 0692da436391 p1, the TendSign cover with the agreement period.
ITK1_COVER = (
    heading("Ramavtal"),
    block("2025-08-19"),
    block("Avtalsnamn IT-konsulttjänster - Verksamhetens IT-behov Ref. nr. 23.3-1688-2024"),
    block("Kontaktperson Startdatum 2025-08-19"),
    block("Slutdatum 2029-08-18"),
    block("Förlängning Ingen förlängning"),
)
# 0692da436391 p4: the supplier, its organisation number and the agreement's sequence empty.
ITK1_PARTIES = block(
    "Ramavtal med avtalsnummer 23.3-1688-2024:[XXX], har träffats för Avropsberättigades "
    "räkning, mellan Statens inköpscentral vid Kammarkollegiet, organisationsnummer "
    "202100-0829, nedan Kammarkollegiet, och Leverantör organisationsnummer XXXXXX-XXXX, nedan "
    "Ramavtalsleverantören.",
    4,
)
# 0692da436391 p9 §1.7: the start of the agreement is left to be filled in.
ITK1_IN_FORCE = (
    heading("1.7 Ramavtalets ikraftträdande och löptid", 9),
    block(
        "Ramavtalet blir bindande från och med det datumet som Parterna signerar det men träder "
        "i kraft den [DATUM (dag-mån-år)]. Om Parterna signerar Ramavtalet vid ett senare datum "
        "träder Ramavtalet i kraft från och med det datumet.",
        9,
    ),
)
ITK1_LINK = link(MAIN_ITK1, "Ramavtalets huvuddokument", "ramavtalets-huvuddokument.pdf")


def main_document(*blocks: Block) -> ParsedDocument:
    return document(MAIN_ITK1, *blocks)


def kinds(facts: Sequence[Fact]) -> list[tuple[FactKind, str]]:
    return [(fact.kind, fact.value) for fact in facts]


class TestTemplate:
    def test_the_type_template_makes_a_template(self) -> None:
        # Its only unfilled fields are organisation numbers; the type alone decides.
        parsed = document(
            SECURITY_1,
            heading("Säkerhetsskyddsavtal"),
            heading("1 PARTER"),
            block("[LEVERANTÖR], organisationsnummer XXXXXX-XXXX"),
            block('Nedan kallad "Leverantören".'),
        )
        links = [link(SECURITY_1, "Utkast till Säkerhetsskyddsavtal (Nivå 1)", category=None)]

        extraction = extract_document(parsed, extract(parsed, links).chunked, links, REGISTER)

        assert extraction.metadata.document_type is DocumentType.TEMPLATE
        assert kinds(extraction.facts) == [(FactKind.PLACEHOLDER, "org_number")]
        assert extraction.metadata.is_template

    def test_a_date_field_in_a_file_that_states_no_period_makes_a_template(self) -> None:
        parsed = main_document(*ITK1_IN_FORCE)

        metadata = extract_document(
            parsed, extract(parsed, [ITK1_LINK]).chunked, [ITK1_LINK], REGISTER
        ).metadata

        assert metadata.document_type is DocumentType.MAIN_DOCUMENT
        assert metadata.is_template

    def test_a_date_field_next_to_a_stated_period_is_no_template(self) -> None:
        # The signed main document of 23.3-1688-2024 keeps the template's clause, but
        # its cover states the period: it is the agreement, not a template.
        parsed = main_document(*ITK1_COVER, *ITK1_IN_FORCE)

        extraction = extract_document(
            parsed, extract(parsed, [ITK1_LINK]).chunked, [ITK1_LINK], REGISTER
        )

        assert (FactKind.PLACEHOLDER, "date") in kinds(extraction.facts)
        assert (FactKind.PERIOD_START, "2025-08-19") in kinds(extraction.facts)
        assert not extraction.metadata.is_template

    def test_an_empty_supplier_slot_alone_is_no_template(self) -> None:
        # Every area main document leaves the supplier slot empty.
        parsed = main_document(ITK1_PARTIES)

        extraction = extract_document(
            parsed, extract(parsed, [ITK1_LINK]).chunked, [ITK1_LINK], REGISTER
        )

        placeholders = {f.value for f in extraction.facts if f.kind is FactKind.PLACEHOLDER}
        assert placeholders == {"agreement_number", "org_number", "party"}
        assert not extraction.metadata.is_template


class TestLinks:
    def test_the_title_is_the_link_text_used_most_often(self) -> None:
        # 93bd3b2bfeae is linked from 15 pages, twice with a typo.
        titles = [
            *["Utkast till Säkerhetsskyddsavtal (Nivå 1)"] * 13,
            "Utkast till Säkerhetssyddsavtal (Nivå 1)",
            "Utkast till Säkerhetsskyddsavtal (Nivå1)",
        ]
        links = [
            link(SECURITY_1, title, page=f"sida-{number}") for number, title in enumerate(titles)
        ]
        parsed = document(SECURITY_1, heading("Säkerhetsskyddsavtal"))

        metadata = extract_document(
            parsed, extract(parsed, links).chunked, links, REGISTER
        ).metadata

        assert metadata.title == "Utkast till Säkerhetsskyddsavtal (Nivå 1)"

    def test_a_tie_between_titles_goes_to_the_shorter_whatever_the_order(self) -> None:
        titles = [
            "Utkast till Säkerhetsskyddsavtal (Nivå 1)",
            "Utkast till Säkerhetsskyddsavtal (Nivå1)",
        ]
        parsed = document(SECURITY_1, heading("Säkerhetsskyddsavtal"))
        found = set()
        for ordered in (titles, titles[::-1]):
            links = [link(SECURITY_1, title, page=title) for title in ordered]
            chunked = extract(parsed, links).chunked
            found.add(extract_document(parsed, chunked, links, REGISTER).metadata.title)

        assert found == {"Utkast till Säkerhetsskyddsavtal (Nivå1)"}

    def test_site_updated_is_the_latest_of_the_links(self) -> None:
        links = [
            link(DATA_SHARING, "Utkast till datadelningsavtal", site_updated=date(2023, 2, 14)),
            link(DATA_SHARING, "Utkast till datadelningsavtal", site_updated=date(2026, 3, 2)),
            link(DATA_SHARING, "Utkast till datadelningsavtal", site_updated=None),
        ]
        parsed = document(DATA_SHARING, heading("Datadelningsavtal"))

        metadata = extract_document(
            parsed, extract(parsed, links).chunked, links, REGISTER
        ).metadata

        assert metadata.site_updated == date(2026, 3, 2)


# 185872a6bb90, the signed agreement of Crayon AB; the signers and their ids are made up.
CRAYON_CARD = document(
    CARD_CRAYON,
    block(
        "23.3.2649-22 Programvaror och tjänster - Informationsförsörjning", 3, BlockKind.PAGE_HEADER
    ),
    heading("1.2 Allmänt", 3),
    block(
        "Detta Huvuddokument innehåller krav och villkor för fullgörande av Ramavtalet avseende "
        "Programvaror och tjänster - Informationsförsörjning med diarienummer 23.3.2649-22 och är "
        "tillämpligt på avtalsförhållandet mellan Kammarkollegiet och Ramavtalsleverantören.",
        3,
    ),
    heading("1.3.1 Parter", 3),
    block(
        "Ramavtal med avtalsnummer 23.3.2649-22-003, har träffats för Avropsberättigades räkning, "
        "mellan Statens inköpscentral vid Kammarkollegiet, organisationsnummer 202100-0829, nedan "
        "Kammarkollegiet, och Crayon AB, organisationsnummer 556635-9799, nedan "
        "Ramavtalsleverantören.",
        3,
    ),
    block(
        "Signaturerna i detta dokument är juridiskt bindande. Dokumentet är signerat med Addo "
        "Sign säkra digitala signatur. Undertecknarens identitet registreras fysiskt i det "
        "elektroniska PDF-dokumentet och visas nedan.",
        18,
    ),
    heading("Undertecknare", 18),
    block("ERIK EXEMPELSSON", 18),
    block("AbCdEfGhIjKlMnOpQrStUv 2023-02-22 15:14", 18),
    block("Maria Exempel", 18),
    block("ZyXwVuTsRqPoNmLkJiHgFe 2023-02-27 08:39", 18),
    block(
        "Dokumentet är skyddat med ett Adobe CDS-certifikat. När dokumentet öppnas i Adobe Reader "
        "ser det ut att vara signerat genom Addo Sign signeringstjänst.",
        18,
    ),
)
CRAYON_LINK = link(
    CARD_CRAYON,
    "Ramavtal",
    "ramavtal-informationsforsorjning_crayon.pdf",
    category=None,
    agreement_number="23.3-2649-2022-003",
    site_updated=date(2023, 2, 27),
    page="informationsforsorjning",
)


class TestSignedCard:
    def test_the_last_signature_dates_the_document(self) -> None:
        # No template version, TendSign date, letterhead or dated file name: the
        # signatures are the only date (document_type.version_date, "signed").
        chunked = extract(CRAYON_CARD, [CRAYON_LINK]).chunked

        metadata = extract_document(CRAYON_CARD, chunked, [CRAYON_LINK], REGISTER).metadata

        assert metadata.version_date == date(2023, 2, 27)
        assert metadata.version_rule == "signed"

    def test_the_facts_of_a_signed_agreement(self) -> None:
        chunked = extract(CRAYON_CARD, [CRAYON_LINK]).chunked

        extraction = extract_document(CRAYON_CARD, chunked, [CRAYON_LINK], REGISTER)

        assert kinds(extraction.facts) == [
            (FactKind.PROCUREMENT_NUMBER, "23.3-2649-2022"),  # page header
            (FactKind.PROCUREMENT_NUMBER, "23.3-2649-2022"),  # §1.2
            (FactKind.AGREEMENT_NUMBER, "23.3-2649-2022-003"),  # §1.3.1
            (FactKind.ORG_NUMBER, "202100-0829"),
            (FactKind.ORG_NUMBER, "556635-9799"),
            (FactKind.PARTY, "556635-9799"),
            (FactKind.SIGNED_ON, "2023-02-22"),
            (FactKind.SIGNED_ON, "2023-02-27"),
        ]
        assert extraction.metadata.document_type is DocumentType.SUPPLIER_AGREEMENT
        assert extraction.metadata.agreement_number == "23.3-2649-2022-003"

    def test_a_cards_agreement_number_is_written_as_the_register_writes_it(self) -> None:
        # Made up: every card link of the pilot writes the number as the register does.
        card = CRAYON_LINK.model_copy(update={"agreement_number": "23.3.2649-22-003"})
        chunked = extract(CRAYON_CARD, [card]).chunked

        metadata = extract_document(CRAYON_CARD, chunked, [card], REGISTER).metadata

        assert metadata.agreement_number == "23.3-2649-2022-003"


def test_the_facts_of_all_rules_are_in_block_order() -> None:
    # Each rule module gives its facts in block order; together they are sorted by
    # block, and within a block the identifiers come before the parties and dates.
    parsed = main_document(*ITK1_COVER, ITK1_PARTIES)

    extraction = extract_document(
        parsed, extract(parsed, [ITK1_LINK]).chunked, [ITK1_LINK], REGISTER
    )

    assert [(fact.block, fact.kind, fact.value) for fact in extraction.facts] == [
        (2, FactKind.PROCUREMENT_NUMBER, "23.3-1688-2024"),  # "Ref. nr." on the cover
        (3, FactKind.PERIOD_START, "2025-08-19"),
        (3, FactKind.PERIOD_END, "2029-08-18"),
        (3, FactKind.EXTENSION_MONTHS, "0"),
        (6, FactKind.PROCUREMENT_NUMBER, "23.3-1688-2024"),  # "23.3-1688-2024:[XXX]"
        (6, FactKind.PLACEHOLDER, "agreement_number"),
        (6, FactKind.ORG_NUMBER, "202100-0829"),
        (6, FactKind.PLACEHOLDER, "org_number"),
        (6, FactKind.PLACEHOLDER, "party"),
    ]


# --- The corpus -------------------------------------------------------------------------------

# 14aa1cc8ee3d: one reference names §9.15 exactly (R4 resolves it), the other with
# another word ending, which only the language model can match.
EXACT = (
    "Ramavtalsleverantörens skadeståndsansvar är vid var tid begränsat till ett belopp "
    "uppgående till Ramavtalsleverantörs totala försäljning genom Ramavtalet sedan dess "
    "ingående enligt avsnitt Försäljningsredovisning och administrationsavgift."
)
VARIANT = (
    "Vite utgår med 0,25 % av Ramavtalsleverantörens rapporterade omsättning på Ramavtalet "
    "(enligt avsnitt Försäljningsredovisning och administrativ avgift) de fyra (4) senaste "
    "kvartalen för varje påbörjad sjudagarsperiod som det väsentliga avtalsbrottet kvarstår."
)
TARGET = "9.15 Försäljningsredovisning och administrationsavgift"
CARD_026_INPUT = extract(
    document(
        CARD_026,
        block("IT-konsulttjänster 2020 Dnr 23.3-2940-20 Ramavtal 23.3.2940-20:026 Experis AB"),
        heading(TARGET, 19),
        heading("9.18 Avtalsbrott och påföljder", 23),
        heading("9.18.2 Vite vid Ramavtalsleverantörens väsentliga avtalsbrott", 23),
        block(VARIANT, 23),
        heading("9.18.5 Skadeståndsansvar", 24),
        block(EXACT, 24),
    ),
    [
        link(
            CARD_026,
            "Ramavtal",
            "ramavtal-arkitektur-och-utveckling-experis.pdf",
            category=None,
            agreement_number="23.3-2940-20:026",
            page="it-konsulttjanster-4.-arkitektur-och-utveckling",
        )
    ],
)


class FakeMatcher:
    """Answers with `answer` when it is a candidate, else with what it is told; counts calls."""

    def __init__(self, answer: str | None) -> None:
        self.answer = answer
        self.calls = 0
        self.questions: list[tuple[str, list[str]]] = []

    def __call__(self, phrase: str, context: str, candidates: Sequence[str]) -> str | None:
        self.calls += 1
        self.questions.append((phrase, list(candidates)))
        return self.answer


class TestCorpus:
    def test_without_a_matcher_the_rules_decide(self) -> None:
        corpus = extract_corpus([CARD_026_INPUT], REGISTER)

        statuses = [(r.status, r.rule) for r in corpus.references]
        assert statuses == [(ReferenceStatus.TITLE_MISSING, "R4"), (ReferenceStatus.RESOLVED, "R4")]
        assert corpus.rules_rate == 0.5
        assert resolution_rate(corpus.references) == 0.5
        assert corpus.match_stats is None

    def test_the_matcher_resolves_what_the_rules_left(self) -> None:
        matcher = FakeMatcher(TARGET)

        corpus = extract_corpus([CARD_026_INPUT], REGISTER, matcher)

        [(phrase, candidates)] = matcher.questions
        assert phrase.startswith("Försäljningsredovisning och administrativ avgift)")
        assert TARGET in candidates
        assert [(r.status, r.rule) for r in corpus.references] == [
            (ReferenceStatus.RESOLVED, "R4-llm"),
            (ReferenceStatus.RESOLVED, "R4"),
        ]
        # The rate by the rules alone is kept apart from the final one.
        assert corpus.rules_rate == 0.5
        assert resolution_rate(corpus.references) == 1.0
        assert corpus.match_stats == MatchStats(asked=1, answered=1, calls=1, cache_hits=0)

    def test_an_answer_that_is_no_candidate_changes_nothing(self) -> None:
        corpus = extract_corpus([CARD_026_INPUT], REGISTER, FakeMatcher("9.18 Avtalsbrott"))

        assert resolution_rate(corpus.references) == 0.5
        assert corpus.match_stats == MatchStats(asked=1, answered=0, calls=1, cache_hits=0)

    def test_each_file_gets_its_extraction_in_input_order(self) -> None:
        crayon = extract(CRAYON_CARD, [CRAYON_LINK])

        corpus = extract_corpus([CARD_026_INPUT, crayon], REGISTER)

        assert [e.metadata.sha256 for e in corpus.extractions] == [CARD_026, CARD_CRAYON]
        own = [f for f in corpus.extractions[0].facts if f.kind is FactKind.AGREEMENT_NUMBER]
        assert [(f.value, f.raw) for f in own] == [("23.3-2940-20:026", "23.3.2940-20:026")]
