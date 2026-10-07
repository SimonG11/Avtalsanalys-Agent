"""Tests for avtalsagent.ingestion.step6_index.

The chunk texts are verbatim sentences of pilot documents: the penalty clause
6.21.4 of IT-drift Mindre's general terms (0a5491b1398e p. 24) and the price
adjustment clause 1.10.4 of a main document (0692da436391 p. 14). The register
rows are those of the 2026-10-05 list (IT-drift Mindre, 23.3-5890-2023, and
Bemanningstjänster, 23.3-14537-2023).
"""

import hashlib
from datetime import date

from avtalsagent.domain.documents import CatalogLink
from avtalsagent.domain.extracted import Quarantine
from avtalsagent.domain.register import RegisterEntry
from avtalsagent.domain.search import ChunkKey, DocumentScope
from avtalsagent.ingestion.step6_index import (
    IndexSource,
    agreement_procurements,
    document_scopes,
    embedded_text,
    plan_index,
    section_hash,
    text_hash,
)
from avtalsagent.retrieval import swedish_text

TERMS = "a" * 64  # IT-drift Mindre: Allmänna villkor
PROCUREMENT = "b" * 64  # IT-drift Mindre: Upphandlingsdokument, with the terms as chapter 6
CARD = "c" * 64  # IT-drift Mindre: Advania's card
TEMPLATE = "d" * 64  # linked from IT-drift Mindre and Bemanningstjänster

PENALTY = (
    "6.21.4 Ansvar och vite vid övriga avtalsbrott\n\n"
    "Vitets storlek uppgår till 25 000 SEK och utgår per påbörjad vecka som bristen kvarstår. "
    "Vite ska högst uppgå till 100 000 SEK. Understiger Kontraktets värde 100 000 SEK ska "
    "vitet högst uppgå till Kontraktets värde.\n\n"
    "Om bristen inte åtgärdas utgår maximalt vite."
)
PRICE_ADJUSTMENT = (
    "1.10.4 Prisjustering\n\n"
    "Prisjustering enligt detta avsnitt omfattar inte redan tecknade Kontrakt. Kontrakt kan "
    "prisjusteras enligt bilaga Allmänna villkor."
)
TERMS_HEADER = (
    "IT-drift (23.3-5890-2023) › Allmänna villkor › 6 Allmänna villkor › "
    "6.21 Avtalsbrott och påföljder › 6.21.4 Ansvar och vite vid övriga avtalsbrott"
)
PROCUREMENT_HEADER = TERMS_HEADER.replace("Allmänna villkor ›", "Upphandlingsdokument ›", 1)


def source(sha256: str, section: int, position: int, text: str, section_text: str) -> IndexSource:
    return IndexSource(
        key=ChunkKey(sha256, section, position),
        context_header=TERMS_HEADER if sha256 == TERMS else PROCUREMENT_HEADER,
        text=text,
        section_text=section_text,
        section_number=section_text.split(" ", 1)[0],
    )


# The penalty section in two chunks, in the terms and in the procurement document.
FIRST, SECOND = PENALTY.split("\n\nOm bristen")
SOURCES = [
    source(TERMS, 40, 0, FIRST, PENALTY),
    source(TERMS, 40, 1, "Om bristen" + SECOND, PENALTY),
    source(PROCUREMENT, 12, 0, FIRST, PENALTY),
    source(PROCUREMENT, 12, 1, "Om bristen" + SECOND, PENALTY),
    source(PROCUREMENT, 7, 0, PRICE_ADJUSTMENT, PRICE_ADJUSTMENT),
]
NOTHING_HELD = Quarantine(files=frozenset(), sections=frozenset())


class TestHashes:
    def test_the_embedded_text_is_the_header_a_blank_line_and_the_chunk(self) -> None:
        assert embedded_text(TERMS_HEADER, FIRST) == f"{TERMS_HEADER}\n\n{FIRST}"

    def test_the_text_hash_is_the_sha256_of_the_utf8_text(self) -> None:
        text = "Vite vid försening"

        assert text_hash(text) == hashlib.sha256(text.encode("utf-8")).hexdigest()
        assert text_hash(text) != text_hash(text.replace("ö", "o"))

    def test_copies_that_differ_only_in_whitespace_share_the_section_hash(self) -> None:
        # Docling ends lines and paragraphs differently in two printings of one text.
        reflowed = PENALTY.replace("\n\n", "\n").replace("25 000", "25  000")

        assert section_hash(reflowed, "6.21.4") == section_hash(PENALTY, "6.21.4")
        assert section_hash(PRICE_ADJUSTMENT, "1.10.4") != section_hash(PENALTY, "6.21.4")

    def test_the_hash_leaves_out_the_section_number_but_keeps_the_title(self) -> None:
        # "Prismodeller" is 6.17 of one set of general terms and 7.16 of a procurement
        # document in the pilot (c3cd0b4813a8, 66b60a8f74a0): one clause, two numbers.
        renumbered = PENALTY.replace("6.21.4", "7.20.4", 1)
        body = PENALTY.removeprefix("6.21.4")

        assert section_hash(renumbered, "7.20.4") == section_hash(PENALTY, "6.21.4")
        assert section_hash(PENALTY, "6.21.4") == text_hash(" ".join(body.split()))
        # With a dot after the number, as some documents print it.
        assert section_hash("6.21.4. " + body.lstrip(), "6.21.4") == section_hash(PENALTY, "6.21.4")
        # The title stays: the same sentence under another heading is another clause.
        retitled = PENALTY.replace("Ansvar och vite", "Vite", 1)
        assert section_hash(retitled, "6.21.4") != section_hash(PENALTY, "6.21.4")

    def test_only_the_whole_number_is_left_out(self) -> None:
        # "6.1" does not begin "6.17 Prismodeller", and a number later in the text stays.
        text = "6.17 Prismodeller\n\nSe punkt 6.1."

        assert section_hash(text, "6.1") == text_hash(" ".join(text.split()))
        assert section_hash(text, None) == text_hash(" ".join(text.split()))
        assert section_hash(text, "6.17") == text_hash("Prismodeller Se punkt 6.1.")


class TestPlanIndex:
    def test_every_chunk_is_indexed_in_key_order(self) -> None:
        plan = plan_index(reversed(SOURCES), NOTHING_HELD)

        assert [chunk.key for chunk in plan.chunks] == sorted(item.key for item in SOURCES)
        assert plan.held_back == 0

    def test_a_chunk_carries_its_text_hashes_and_stems(self) -> None:
        [chunk] = [
            c for c in plan_index(SOURCES, NOTHING_HELD).chunks if c.key.section_position == 7
        ]

        text = f"{PROCUREMENT_HEADER}\n\n{PRICE_ADJUSTMENT}"
        assert chunk.embedded_text == text
        assert chunk.text_hash == text_hash(text)
        assert chunk.section_hash == section_hash(PRICE_ADJUSTMENT, "1.10.4")
        # The header's words are indexed with the chunk's: "IT-drift", the procurement.
        assert chunk.terms == tuple(swedish_text.terms(text))
        assert {"it", "drift", "23.3-5890-2023"} <= set(chunk.terms)

    def test_copies_of_a_section_share_its_hash_but_not_their_text_hashes(self) -> None:
        plan = plan_index(SOURCES, NOTHING_HELD)
        by_key = {chunk.key: chunk for chunk in plan.chunks}
        in_terms = by_key[ChunkKey(TERMS, 40, 0)]
        in_procurement = by_key[ChunkKey(PROCUREMENT, 12, 0)]

        # One group for the search, but two embeddings: the headers name other documents.
        assert in_terms.section_hash == in_procurement.section_hash
        assert in_terms.section_hash == by_key[ChunkKey(TERMS, 40, 1)].section_hash
        assert in_terms.text_hash != in_procurement.text_hash

    def test_held_back_files_and_sections_are_left_out_and_counted(self) -> None:
        held = Quarantine(files=frozenset({TERMS}), sections=frozenset({(PROCUREMENT, 12)}))

        plan = plan_index(SOURCES, held)

        assert [chunk.key for chunk in plan.chunks] == [ChunkKey(PROCUREMENT, 7, 0)]
        assert plan.held_back == 4

    def test_an_unchecked_file_is_held_back(self) -> None:
        # Fail closed: a file steps 4 and 5 have not run on is in `files` too.
        held = Quarantine(
            files=frozenset({PROCUREMENT}), sections=frozenset(), unchecked=frozenset({PROCUREMENT})
        )

        plan = plan_index(SOURCES, held)

        assert {chunk.key.sha256 for chunk in plan.chunks} == {TERMS}
        assert plan.held_back == 3


# --- Scopes -----------------------------------------------------------------------------------

IT_DRIFT_PAGE = "https://www.avropa.se/ramavtal/ramavtalsomraden/it-drift/it-drift-mindre/"
BEMANNING_PAGE = "https://www.avropa.se/ramavtal/ramavtalsomraden/bemanningstjanster/it/"


def link(
    sha256: str,
    title: str,
    page_url: str = IT_DRIFT_PAGE,
    agreement_number: str | None = None,
    procurement_numbers: tuple[str, ...] = ("23.3-5890-2023",),
) -> CatalogLink:
    page_title = (
        "IT-drift Mindre, upp till 200 anställda"
        if page_url == IT_DRIFT_PAGE
        else "Bemanningstjänster - IT-tjänster upp till 1000 timmar"
    )
    return CatalogLink(
        sha256=sha256,
        url=f"https://www.avropa.se/globalassets/{sha256[:4]}.pdf",
        title=title,
        category=None if agreement_number else "Avtal",
        agreement_number=agreement_number,
        site_updated=None,
        page_url=page_url,
        page_title=page_title,
        page_procurement_numbers=procurement_numbers,
        page_period=None,
    )


def entry(agreement_number: str, supplier: str, area: str) -> RegisterEntry:
    return RegisterEntry(
        agreement_number=agreement_number,
        procurement_number=agreement_number.rsplit("-", 1)[0],
        org_number="556214-9996",
        supplier_name=supplier,
        former_supplier_name=None,
        framework_area=area,
        sub_area_path=(area,),
        valid_from=date(2024, 11, 14),
        valid_to=date(2028, 11, 13),
        max_extension_to=None,
    )


REGISTER = [
    entry("23.3-5890-2023-003", "Advania Sverige AB", "IT-drift"),
    entry("23.3-5890-2023-001", "NetBin Sverige AB", "IT-drift"),
    entry("23.3-14537-2023-001", "A Hub Group AB", "Bemanningstjänster"),
]
BEMANNING = ("23.3-14537-2023",)


class TestDocumentScopes:
    def test_a_shared_file_has_its_page_area_procurement_and_no_agreement(self) -> None:
        scopes = document_scopes(
            [link(TERMS, "Allmänna villkor")], REGISTER, {TERMS: None}, {TERMS: "general_terms"}
        )

        assert scopes == {
            TERMS: DocumentScope(
                sha256=TERMS,
                framework_areas=("IT-drift",),
                procurement_numbers=("23.3-5890-2023",),
                agreement_numbers=(),
                page_titles=("IT-drift Mindre, upp till 200 anställda",),
                document_type="general_terms",
            )
        }

    def test_a_card_has_the_agreement_numbers_of_its_links_and_its_metadata(self) -> None:
        links = [link(CARD, "Ramavtal", agreement_number="23.3-5890-2023-003")]

        scopes = document_scopes(links, REGISTER, {CARD: "23.3-5890-2023-003"}, {})
        only_metadata = document_scopes(
            [link(CARD, "Ramavtal")], REGISTER, {CARD: "23.3-5890-2023-003"}, {}
        )

        assert scopes[CARD].agreement_numbers == ("23.3-5890-2023-003",)
        assert only_metadata[CARD].agreement_numbers == ("23.3-5890-2023-003",)

    def test_a_file_on_two_pages_has_both_areas_procurements_and_titles(self) -> None:
        links = [
            link(TEMPLATE, "Utkast till Säkerhetsskyddsavtal"),
            link(
                TEMPLATE,
                "Utkast till Säkerhetsskyddsavtal",
                BEMANNING_PAGE,
                procurement_numbers=BEMANNING,
            ),
        ]

        scope = document_scopes(links, REGISTER, {}, {})[TEMPLATE]

        assert scope.framework_areas == ("Bemanningstjänster", "IT-drift")
        assert scope.procurement_numbers == ("23.3-14537-2023", "23.3-5890-2023")
        assert scope.page_titles == (
            "Bemanningstjänster - IT-tjänster upp till 1000 timmar",
            "IT-drift Mindre, upp till 200 anställda",
        )
        assert scope.agreement_numbers == ()

    def test_numbers_are_found_by_key_and_stored_as_the_register_spells_them(self) -> None:
        # The documents write the case number with dots and a two-digit year; a page could
        # too. A search filter is spelt as in the register, so the scope must be as well.
        links = [
            link(
                CARD,
                "Ramavtal",
                agreement_number="23.3.5890-23-003",
                procurement_numbers=("23.3.5890-23",),
            )
        ]

        scope = document_scopes(links, REGISTER, {CARD: "23.3-5890-2023:03"}, {})[CARD]

        assert scope.framework_areas == ("IT-drift",)
        assert scope.procurement_numbers == ("23.3-5890-2023",)
        assert scope.agreement_numbers == ("23.3-5890-2023-003",)

    def test_a_number_the_register_lacks_is_kept_as_written(self) -> None:
        links = [link(TERMS, "Ramavtal", procurement_numbers=("23.3-2283-2022",))]

        assert document_scopes(links, REGISTER, {}, {})[TERMS].procurement_numbers == (
            "23.3-2283-2022",
        )

    def test_a_procurement_the_register_lacks_gives_no_area(self) -> None:
        # A page whose procurement this register has no agreement of: no area, as in step 3.
        links = [link(TERMS, "Ramavtal", procurement_numbers=("6765/05",))]

        assert document_scopes(links, REGISTER, {}, {})[TERMS].framework_areas == ()

    def test_only_linked_files_get_a_scope(self) -> None:
        scopes = document_scopes([link(TERMS, "Allmänna villkor")], REGISTER, {CARD: None}, {})

        assert list(scopes) == [TERMS]

    def test_a_file_step_4_has_not_typed_has_no_document_type(self) -> None:
        scopes = document_scopes(
            [link(TERMS, "Allmänna villkor")], REGISTER, {}, {CARD: "template"}
        )

        assert scopes[TERMS].document_type is None


def test_each_agreement_has_its_procurement_from_the_register() -> None:
    assert agreement_procurements(REGISTER) == {
        "23.3-5890-2023-003": "23.3-5890-2023",
        "23.3-5890-2023-001": "23.3-5890-2023",
        "23.3-14537-2023-001": "23.3-14537-2023",
    }
