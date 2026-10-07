"""Tests for avtalsagent.ingestion.extract.document_type.

The link titles, file names and categories are those on avropa.se (2026-10-05); the
file each comes from is named by its sha256 prefix next to the case. The blocks are
real lines from the parsed files: TendSign covers and page headers (76dfb5d1ae1f,
f479352f0a55, b2bf8baefe48, a0924d85e62c, 124cb5f66cc3, 14aa1cc8ee3d), template
headers (3117fd65796c, 232f65cf161a, 143bbcc43f2d, 145e34c51489) and letterheads
(4f886a784c5d, 1d58dc2e8387, 171a3cacf5fd, 4b6c2a533fae, 3b22b96ca023); some file
names are shortened. Made up, since no pilot file has them: the titles under F2 and
F3, the impossible dates, the file names "prislista-231345" and "ordernummer-2011305",
the lines "Version 1: ..." and "Version 2: ...", and the signer id in the page header.
"""

from datetime import date

import pytest

from avtalsagent.domain.documents import CatalogLink
from avtalsagent.domain.extracted import DocumentType
from avtalsagent.domain.parsed import Block, BlockKind, ParsedDocument, Section
from avtalsagent.ingestion.extract.document_type import (
    FALLBACK_RULES,
    NO_RULE,
    annex_number,
    classify,
    first_chapter,
    published_on,
    tendsign_cover,
    version_date,
)
from avtalsagent.ingestion.step3_chunk import OutlineKind

BASE = "https://www.avropa.se/globalassets/bilagor/"
AVTAL = "Avtal"
UPPHANDLING = "Upphandling"
STOD = "Stöddokument och länkar"


def link(
    title: str,
    file_name: str = "dokument.pdf",
    category: str | None = AVTAL,
    agreement_number: str | None = None,
) -> CatalogLink:
    return CatalogLink(
        sha256="ab" * 32,
        url=BASE + file_name,
        title=title,
        category=category,
        agreement_number=agreement_number,
        site_updated=date(2025, 8, 19),
        page_url="https://www.avropa.se/ramavtal/ramavtalsomraden/it-drift/",
        page_title="IT-drift",
        page_procurement_numbers=("23.3-5890-2023",),
        page_period="2024-11-14 - 2028-11-13",
    )


def block(text: str, kind: BlockKind = BlockKind.TEXT, page: int | None = 1) -> Block:
    return Block(kind=kind, text=text, page=page)


def header(text: str, page: int | None = 2) -> Block:
    return block(text, BlockKind.PAGE_HEADER, page)


def document(*blocks: Block) -> ParsedDocument:
    return ParsedDocument(sha256="ab" * 32, file_type="pdf", parser="test", pages=(), blocks=blocks)


def section(number: str | None, level: int) -> Section:
    return Section(
        position=0,
        number=number,
        title="",
        level=level,
        parent=None,
        path=(),
        page_start=1,
        page_end=1,
        text="",
    )


# --- classify ------------------------------------------------------------------------------

# (link, type, rule). Each rule has a case that a later rule would type differently, so
# removing or moving the rule fails a case.
CASES = [
    # 14aa1cc8ee3d, in the card of 23.3-2940-20:010
    (
        link("Ramavtal", category=None, agreement_number="23.3-2940-20:010"),
        "SUPPLIER_AGREEMENT",
        "R01",
    ),
    # 3a316e27aadf: R03 would read "Upphandlingsdokument"
    (
        link("Frågor och svar - Upphandlingsdokument", category=UPPHANDLING),
        "QUESTIONS_AND_ANSWERS",
        "R02",
    ),
    (
        link("Ansökningsinbjudan", category=UPPHANDLING),
        "PROCUREMENT_DOCUMENT",
        "R03",
    ),  # 54211e718d8e
    # a0924d85e62c: listed under "Avtal", F1 would make it an annex
    (link("Kvalificering av sökande"), "PROCUREMENT_DOCUMENT", "R03"),
    # 5c9b05f2cc79: only the file name says it is an amendment; R05 would read "Volymavtal"
    (
        link("Volymavtal", "tillaggsavtal-nr-5-volymavtal-ibm-6765_05-kam-110916.pdf"),
        "AMENDMENT",
        "R04",
    ),
    # d54ed0900be5: R14 would read "Bilaga"
    (link("Bilaga 4 Tillägg och förtydliganden till bilaga 4.1"), "AMENDMENT", "R04"),
    (link("Ramavtalets huvuddokument", "ramavtalets-huvuddokument.pdf"), "MAIN_DOCUMENT", "R05"),
    (
        link("Volymavtalets huvudavtal 1.0", "volymavtalets-huvudavtal-1.0.pdf"),
        "MAIN_DOCUMENT",
        "R05",
    ),
    # The annex of IBM's main document has the same title; the file name tells it apart.
    (link("IBM Volymavtal v 1.0", "ibm-volymavtal-2005-v-1.0-050413-bilaga-1.doc"), "ANNEX", "F1"),
    # 255e496fa266: R07 would read "Allmänna villkor"
    (link("IBM Användningsvillkor-Allmänna villkor"), "LICENCE_TERMS", "R06"),
    # 7c093254b164: R08 would read "pris" in "Enterprise"
    (link("Bilaga 5.1 Enterprise-avtal"), "LICENCE_TERMS", "R06"),
    (link("PA Government Attachment"), "LICENCE_TERMS", "R06"),  # b6a94d1c1a90
    (link("Allmänna villkor", "allmanna-villkor.pdf"), "GENERAL_TERMS", "R07"),  # 0a5491b1398e
    (link("Timpriser - sammanställning"), "PRICE_ANNEX", "R08"),  # 33570086cce8
    (link("Redovisning av hållbarhetskrav", category=STOD), "REQUIREMENTS_REPORT", "R09"),
    (link("Utkast till Säkerhetsskyddsavtal (Nivå1)", category=STOD), "TEMPLATE", "R10"),
    (link("Kontraktsmall", category=STOD), "TEMPLATE", "R10"),  # 145e34c51489
    # 6145b5eefb35: R12 would read "Kravkatalog"
    (link("Avropsrutin och Kravkatalog", category=STOD), "ANNEX", "R11b"),
    (link("Byte av konsult", category=STOD), "CALL_OFF_GUIDANCE", "R11"),  # 0c2dd21d474a
    (link("Kravkatalog", "8.kravkatalog.pdf"), "REQUIREMENTS_CATALOGUE", "R12"),  # 1b2527015c7f
    (link("Krav på kompetenser"), "REQUIREMENTS_SPECIFICATION", "R13"),  # da5391afde0d
    (link("Bilaga 1 Kontaktuppgifter", "bilaga-1_kontaktuppgifter.pdf"), "ANNEX", "R14"),
    (link("Nuts 2 indelning", "nuts-2-indelning-scb2.pdf"), "ANNEX", "F1"),  # 19c85c74c3b2
    (link("Rättelse av anbudsformulär", category=UPPHANDLING), "PROCUREMENT_DOCUMENT", "F2"),
    (link("Webbinarium om avrop", category=STOD), "CALL_OFF_GUIDANCE", "F3"),
]


@pytest.mark.parametrize(
    ("catalog_link", "document_type", "rule"),
    CASES,
    ids=[f"{rule}-{case.title}" for case, _, rule in CASES],
)
def test_classify_gives_the_type_of_the_first_rule_that_matches(
    catalog_link: CatalogLink, document_type: str, rule: str
) -> None:
    assert classify([catalog_link]) == (DocumentType[document_type], rule)


def test_a_supplier_title_outside_a_card_is_not_a_supplier_agreement() -> None:
    # The same title with no agreement number and no category: no rule types it.
    assert classify([link("Ramavtal", category=None)]) == (DocumentType.UNKNOWN, NO_RULE)


def test_a_file_without_links_is_unknown() -> None:
    assert classify([]) == (DocumentType.UNKNOWN, NO_RULE)


def test_a_file_on_several_pages_gets_the_earliest_rule_of_its_links() -> None:
    by_title = link("Kravkatalog")  # R12
    by_routine = link("Avropsrutin och Kravkatalog", category=STOD)  # R11b
    assert classify([by_title, by_routine]) == (DocumentType.ANNEX, "R11b")
    assert classify([by_routine, by_title]) == (DocumentType.ANNEX, "R11b")


def test_title_variants_on_different_pages_give_the_same_type() -> None:
    # 93bd3b2bfeae is linked with three spellings.
    titles = [
        "Utkast till Säkerhetsskyddsavtal (Nivå 1)",
        "Utkast till Säkerhetsskyddsavtal (Nivå1)",
        "Utkast till Säkerhetssyddsavtal (Nivå 1)",
    ]
    links = [link(title, "sakerhetsskyddsavtal-niva-1-mall.pdf", STOD) for title in titles]
    assert classify(links) == (DocumentType.TEMPLATE, "R10")


def test_the_fallback_rules_are_the_category_rules() -> None:
    assert {rule for _, _, rule in CASES if rule.startswith("F")} == FALLBACK_RULES


# --- annex_number --------------------------------------------------------------------------


def test_annex_number_from_the_link_text() -> None:
    title = "Bilaga 4.1 Microsoft Business and Services Agreement"  # 005469cd3990
    assert annex_number([link(title, "bilaga-4.1_microsoft-business.pdf")]) == "4.1"
    # d54ed0900be5: the number at the start, not the annex it amends
    assert annex_number([link("Bilaga 4 Tillägg och förtydliganden till bilaga 4.1")]) == "4"


def test_annex_number_from_the_file_name_when_the_text_has_none() -> None:
    ibm = "volymavtal-ibm-bilaga-5.2--pagovernmentattachment_sweden-z125-6501-01-110916.pdf"
    assert annex_number([link("PA Government Attachment", ibm)]) == "5.2"  # b6a94d1c1a90


def test_no_annex_number_from_bilaga_inside_a_word_or_without_a_number() -> None:
    utkast = link("Utkast till datadelningsavtal", "bilaga-utkast-till-datadelningsavtal.docx")
    pris = link("Prisbilaga - sammanställning Delområde 3", "prisbilaga---sammanstallning-3.pdf")
    assert annex_number([utkast]) is None
    assert annex_number([pris]) is None


# --- first_chapter -------------------------------------------------------------------------


def test_first_chapter_of_a_chapter_printed_from_tendsign() -> None:
    # 14aa1cc8ee3d: "9 Ramavtalets Huvuddokument" after the cover text
    sections = [section(None, 0), section("9", 1), section("9.1", 2)]
    assert first_chapter(sections, OutlineKind.NUMBERED) == 9


def test_no_first_chapter_when_the_document_starts_at_1() -> None:
    assert first_chapter([section("1", 1), section("1.1", 2)], OutlineKind.NUMBERED) is None


def test_first_chapter_from_a_subsection_when_the_chapter_heading_is_missing() -> None:
    # e04bad6a0ced: its first pages have no text layer, so the first section is 7.16.
    assert first_chapter([section(None, 0), section("7.16", 2)], OutlineKind.NUMBERED) == 7


def test_a_questions_log_has_no_chapter() -> None:
    # 20ddb9ebf9cc's first question is number 13.
    assert first_chapter([section("13", 1), section("14", 1)], OutlineKind.QUESTIONS) is None


# --- tendsign_cover ------------------------------------------------------------------------


def test_tendsign_cover_word_followed_by_the_print_date() -> None:
    signed = document(block("Ramavtal"), block("2024-09-25"))  # 76dfb5d1ae1f
    tender = document(
        block("Upphandlingsdokument", BlockKind.HEADING), block("2024-11-19")
    )  # b2bf8baefe48
    assert tendsign_cover(signed) == "Ramavtal"
    assert tendsign_cover(tender) == "Upphandlingsdokument"


def test_tendsign_cover_in_one_block() -> None:
    assert tendsign_cover(document(block("Ramavtal 2024-09-27", BlockKind.HEADING))) == "Ramavtal"


def test_no_cover_without_the_print_date() -> None:
    # 14aa1cc8ee3d, a supplier's agreement: the word, but no date after it.
    card = document(
        header("Visma Addo ID-nummer : 00000000-1111-2222-3333-444444444444", page=1),
        block("Ramavtal", BlockKind.HEADING),
        block("Upphandlande organisation", BlockKind.HEADING),
    )
    assert tendsign_cover(card) is None


def test_no_cover_from_a_letterhead_or_later_in_the_document() -> None:
    letterhead = document(header("Datum"), header("2026-03-10"))  # 4b6c2a533fae
    late = document(*[block("Text") for _ in range(6)], block("Inbjudan"), block("2023-10-03"))
    assert tendsign_cover(letterhead) is None
    assert tendsign_cover(late) is None


# --- version_date --------------------------------------------------------------------------


def test_template_version_comes_before_the_file_name() -> None:
    template = document(header("Kammarkollegiets version 4.0 (2021-09-30)", page=None))
    links = [link("Utkast till personuppgiftsbiträdesavtal", "utkast-2024-10-11.docx", STOD)]
    assert version_date(template, links, []) == (date(2021, 9, 30), "template_version")


def test_template_date() -> None:
    malldatum = document(header("Malldatum: 2024-10-11", page=None))  # 232f65cf161a
    mall = document(  # 143bbcc43f2d
        header("Mall för Kontrakt – Programvaror och tjänster | 2026-03-31 |  |", page=None)
    )
    assert version_date(malldatum, [], []) == (date(2024, 10, 11), "template_date")
    assert version_date(mall, [], []) == (date(2026, 3, 31), "template_date")


def test_tendsign_published_comes_before_the_cover_and_the_latest_version_wins() -> None:
    # a0924d85e62c: printed a month after it was published
    printout = document(
        block("Inbjudan", BlockKind.HEADING),
        block("2023-10-03"),
        block(
            "IT-drift 2023, område Mindre 23.3-5890-2023 Publicerad 2023-09-04 13:44 "
            "Sista ansökansdag: 2023-10-04 23:59"
        ),
    )
    revised = document(
        block("Version 2: publicerad 2024-11-08 09:12"),
        block("Version 3: publicerad 2024-11-19 11:47"),  # b2bf8baefe48
    )
    assert version_date(printout, [], []) == (date(2023, 9, 4), "tendsign_published")
    assert version_date(revised, [], []) == (date(2024, 11, 19), "tendsign_published")


def test_tendsign_cover_date_without_a_published_date() -> None:
    signed = document(block("Ramavtal"), block("2024-09-25"))  # 76dfb5d1ae1f
    assert version_date(signed, [], []) == (date(2024, 9, 25), "tendsign_cover")


@pytest.mark.parametrize(
    "text",
    [
        "Datum Sid 2 (27) 2026-08-28 Dnr 23.5-9343-2024 23.5-9345-2024 Statens inköpscentral",
        "2025-06-05",  # 1d58dc2e8387
        "2024-05-01 Dnr 23.5-3718-2024",  # 171a3cacf5fd
    ],
)
def test_letterhead_date_in_a_page_header(text: str) -> None:
    day = version_date(document(header(text)), [], [])
    assert day[1] == "letterhead"


def test_no_letterhead_date_outside_the_page_header_or_inside_other_text() -> None:
    body = document(block("2024-05-01 Dnr 23.5-3718-2024"))
    form = document(header("Ramavtalsområde: | 2024-09-27 Kammarkollegiets diarienr: |"))
    glued = document(header("2023-06-1501\tSida 3 (13)"))  # 3b22b96ca023
    impossible = document(header("Datum 2025-02-30"))
    for doc in (body, form, glued, impossible):
        assert version_date(doc, [], []) == (None, None)


def test_the_last_signature_comes_before_the_file_name() -> None:
    links = [link("Ramavtal", "ramavtal-2023-01-10.pdf", None, "23.3-2649-2022-001")]
    signed = [date(2023, 2, 21), date(2023, 2, 22)]
    assert version_date(document(), links, signed) == (date(2023, 2, 22), "signed")


@pytest.mark.parametrize(
    ("file_name", "day"),
    [
        ("upphandlingsdokument-bemanningstjanster-annonserat-2024-09-24.pdf", date(2024, 9, 24)),
        ("mall-for-kontrakt--programvaror-och-tjanster_20260331.docx", date(2026, 3, 31)),
        ("tillaggsavtal-nr-5-volymavtal-ibm-6765_05-kam-110916.pdf", date(2011, 9, 16)),
    ],
)
def test_date_in_the_file_name(file_name: str, day: date) -> None:
    assert version_date(document(), [link("Dokument", file_name)], []) == (day, "file_name")


def test_the_latest_file_name_date_of_the_links() -> None:
    links = [link("Dokument", "mall-230215.docx"), link("Dokument", "mall-230222.docx")]
    assert version_date(document(), links, []) == (date(2023, 2, 22), "file_name")


@pytest.mark.parametrize(
    "file_name",
    [
        "vagledning-it-konsulttjanster-2026_version-1.02.pdf",  # a version, not a date
        "kontraktsmall-it-drift-mindre-rev-2.docx",
        "volymavtalets-huvudavtal-1.0.pdf",
        "prislista-231345.pdf",  # month 13
        "ordernummer-2011305.pdf",  # seven digits: not "201130" inside a longer number
    ],
)
def test_no_date_in_file_names_without_one(file_name: str) -> None:
    assert version_date(document(), [link("Dokument", file_name)], []) == (None, None)


# --- published_on --------------------------------------------------------------------------


def test_published_on_is_tendsigns_published_date_not_the_print_date() -> None:
    printout = document(  # a0924d85e62c
        block("Inbjudan", BlockKind.HEADING),
        block("2023-10-03"),
        block("IT-drift 2023, område Mindre 23.3-5890-2023 Publicerad 2023-09-04 13:44"),
    )
    assert published_on(printout) == date(2023, 9, 4)


def test_no_published_on_for_a_later_version() -> None:
    later = document(
        block("Upphandlingsdokument", BlockKind.HEADING),
        block("2024-11-19"),
        block("Version 3: publicerad 2024-11-19 11:47"),  # b2bf8baefe48
    )
    first = document(block("Version 1: publicerad 2024-08-19 11:02"))
    assert published_on(later) is None
    assert published_on(first) == date(2024, 8, 19)


def test_published_on_is_the_first_date_near_the_start_otherwise() -> None:
    guide = document(  # 4b6c2a533fae
        block("Vägledning för avrop från ramavtal", BlockKind.HEADING),
        block("Version 1.02"),
        header("Datum"),
        header("2026-03-10"),
        block("Ramavtalet för område 1 är giltigt från och med 2025-08-19"),
    )
    assert published_on(guide) == date(2026, 3, 10)


def test_no_published_on_from_a_date_far_into_the_document() -> None:
    late = document(*[block("Text") for _ in range(40)], block("2026-03-10"))
    assert published_on(late) is None


def test_published_on_skips_impossible_and_glued_dates() -> None:
    doc = document(header("2023-06-1501\tSida 3 (13)"), block("2023-02-30"), block("2023-06-16"))
    assert published_on(doc) == date(2023, 6, 16)
