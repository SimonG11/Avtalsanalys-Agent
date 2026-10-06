"""Tests for avtalsagent.ingestion.checks.missing_text.

The files are real, from the pilot, with their page counts, the pages step 2
found without a text layer, and their sections' pages and first lines:
e04bad6a0ced "Allmänna villkor" (Systemutveckling, 31 pages, 22 without text;
§7.16 p14-22 "7.16 Prismodeller Kund och Ramavtalsleverantör kan avtala om olika
prismodeller", §7.24 p22-23 "7.24 Rättighetsintrång Ramavtalsleverantör ansvarar
för att denne är innehavare av samtliga rättigheter"); 21dd4fde89d5 and
5c9b05f2cc79, IBM's "Tilläggsavtal nr 7" (10 pages) and "nr 5" (1 page), linked as
"Volymavtal" and without text; 124261dc2ad4 "Kravkatalog" (11 pages, p1-3 and p11
without text, sections on p4-10, two each named "Tillgänglighet" and "Utbildning",
on p4 and p10; the last, "Utbildning" on p10, stops mid-sentence and goes on on
p11). Made up, and said so where used: the scanned p4 and p10 in the last test,
the pages of the test of a section between two scanned pages, and the Word file.
"""

from avtalsagent.domain.documents import CatalogLink
from avtalsagent.domain.extracted import (
    DocumentExtraction,
    DocumentMetadata,
    DocumentType,
    Finding,
    Severity,
)
from avtalsagent.domain.parsed import Section
from avtalsagent.ingestion.checks.context import CheckContext, CheckedFile
from avtalsagent.ingestion.checks.missing_text import NO_SECTION, NOT_PARSED, run

TERMS = "e04bad6a0ced579b2cfaf5953c6edd5547acc4e5657cb1097d0d8e189f0a498b"
AMENDMENT_7 = "21dd4fde89d567cfe9a13005bc278f1fd232262f3819b98aaa6ae203d0cbe0b8"
AMENDMENT_5 = "5c9b05f2cc79e6014b60615b4edf47f6802bab7a3f0bcfe93b06b269af73ab06"
CATALOGUE = "124261dc2ad446f35c7b44a450db1c853cdf0436a320bcca75ca3e00c8d31e72"
WORD = "ab" * 32
IBM_PAGE = "Volymavtal för IBM"
TERMS_OCR = (1, 6, 7, 8, 9, 10, 11, 12, 13, 15, 16, 17, 18, 19, 20, 21, 24, 25, 27, 28, 29, 30)


def section(
    position: int, number: str | None, title: str, start: int | None, end: int | None, text: str
) -> Section:
    return Section(
        position=position,
        number=number,
        title=title,
        level=2 if number else 0,
        parent=None,
        path=(f"{number} {title}",) if number else (),
        page_start=start,
        page_end=end,
        text=text,
    )


TERMS_SECTIONS = (
    section(
        0,
        None,
        "Text före första rubriken",
        2,
        14,
        "annat uttryckligen anges i Allmänna villkor eller uppenbarligen framgår av "
        "omständigheterna.",
    ),
    section(
        1,
        "7.16",
        "Prismodeller",
        14,
        22,
        "7.16 Prismodeller\n\nKund och Ramavtalsleverantör kan avtala om olika prismodeller",
    ),
    section(
        2,
        "7.24",
        "Rättighetsintrång",
        22,
        23,
        "7.24 Rättighetsintrång\n\nRamavtalsleverantör ansvarar för att denne är innehavare "
        "av samtliga rättigheter",
    ),
    section(
        3,
        "7.25",
        "Uppföljning",
        23,
        31,
        "7.25 Uppföljning\n\nKund har rätt att kontrollera att Ramavtalsleverantör följer de krav",
    ),
    section(
        4,
        "7.34",
        "Tillämplig lag och tvistelösning",
        31,
        31,
        "7.34 Tillämplig lag och tvistelösning\n\nRättigheter och skyldigheter enligt Kontrakt "
        "regleras av svensk rätt",
    ),
)


def link(sha256: str, title: str, page_title: str) -> CatalogLink:
    return CatalogLink(
        sha256=sha256,
        url=f"https://www.avropa.se/contentassets/{sha256[:12]}.pdf",
        title=title,
        category="Avtal",
        agreement_number=None,
        site_updated=None,
        page_url=f"https://www.avropa.se/ramavtal/{page_title}",
        page_title=page_title,
        page_procurement_numbers=("23.3-2651-2022",),
        page_period=None,
    )


def checked(
    sha256: str,
    sections: tuple[Section, ...],
    page_count: int,
    ocr_pages: tuple[int, ...],
    title: str = "Allmänna villkor",
    page_title: str = "Systemutveckling",
) -> CheckedFile:
    metadata = DocumentMetadata(
        sha256=sha256,
        title=title,
        document_type=DocumentType.GENERAL_TERMS,
        type_rule="R07",
        agreement_number=None,
        annex_number=None,
        first_chapter=None,
        tendsign_cover=None,
        is_template=False,
        version_date=None,
        version_rule=None,
        published_on=None,
        site_updated=None,
    )
    extraction = DocumentExtraction(metadata=metadata, facts=(), mentions=())
    file_type = "pdf" if page_count else "docx"
    links = (link(sha256, title, page_title),)
    return CheckedFile(extraction, links, sections, file_type, page_count, ocr_pages)


def check(*files: CheckedFile, links: tuple[CatalogLink, ...] = ()) -> list[Finding]:
    all_links = tuple(link for file in files for link in file.links) + links
    return run(CheckContext(files=files, links=all_links, register=(), areas=()))


def test_a_section_over_a_page_without_text_is_held_back() -> None:
    findings = check(checked(TERMS, TERMS_SECTIONS, 31, TERMS_OCR))

    sections = [f for f in findings if f.section is not None]
    # 7.24 runs over p22-23, between the scanned p15-21 and p24-25: it has all its text.
    assert [(f.severity, f.section, f.subject) for f in sections] == [
        (Severity.QUARANTINE, 0, "Text före första rubriken"),
        (Severity.QUARANTINE, 1, "7.16"),
        (Severity.QUARANTINE, 3, "7.25"),
    ]
    assert sections[1] == Finding(
        check="missing_text",
        severity=Severity.QUARANTINE,
        subject="7.16",
        message=(
            "Avsnitt 7.16, s. 14-22, omfattar sidorna 15-21 som saknar textlager (ingen OCR "
            "körs), så avsnittets text är ofullständig."
        ),
        sha256=TERMS,
        section=1,
    )
    assert sections[0].message.startswith('Avsnittet "Text före första rubriken", s. 2-14,')


def test_a_file_with_pages_without_text_gets_a_note() -> None:
    findings = check(checked(TERMS, TERMS_SECTIONS, 31, TERMS_OCR))

    assert findings[-1] == Finding(
        check="missing_text",
        severity=Severity.NOTE,
        subject="s. 1, 6-13, 15-21, 24-25, 27-30",
        message=(
            "22 av dokumentets 31 sidor saknar textlager (ingen OCR körs): s. 1, 6-13, 15-21, "
            "24-25, 27-30. Det som står på dem finns inte i dokumentets avsnitt."
        ),
        sha256=TERMS,
    )


# 124261dc2ad4: its first section, and the two named "Utbildning".
CATALOGUE_SECTIONS = (
    section(0, None, "Test", 4, 4, "Test\n\nMed Test avses testledning samt planering för"),
    section(2, None, "Utbildning", 4, 4, "Utbildning\n\nMed Utbildning avses planering och"),
    section(
        49,
        None,
        "Utbildning",
        10,
        10,
        "Utbildning\n\nVid Avrop kan krav komma att ställas på vilken pedagogik, metodik eller "
        "verktyg som används under",
    ),
)


def test_a_section_followed_by_scanned_pages_is_held_back() -> None:
    # Step 3 ends a section on its last page with text: "Utbildning" ends on p10 in the
    # middle of a sentence, whose rest is on the scanned p11. The cover pages p1-3 come
    # before the first section and lose no section's text: they are only in the note.
    findings = check(checked(CATALOGUE, CATALOGUE_SECTIONS, 11, (1, 2, 3, 11), "Kravkatalog"))

    assert findings == [
        Finding(
            check="missing_text",
            severity=Severity.QUARANTINE,
            subject="Utbildning (s. 10)",
            message=(
                'Avsnittet "Utbildning", s. 10, följs av sidan 11 som saknar textlager (ingen '
                "OCR körs), så avsnittets text kan fortsätta där och är då ofullständig."
            ),
            sha256=CATALOGUE,
            section=49,
        ),
        Finding(
            check="missing_text",
            severity=Severity.NOTE,
            subject="s. 1-3, 11",
            message=(
                "4 av dokumentets 11 sidor saknar textlager (ingen OCR körs): s. 1-3, 11. Det "
                "som står på dem finns inte i dokumentets avsnitt."
            ),
            sha256=CATALOGUE,
        ),
    ]


def test_scanned_pages_before_the_next_section_go_with_the_one_before() -> None:
    # Made up: the first section has a scanned page in it (p3) and one after it (p5);
    # the next section starts on p6, which has text.
    sections = (
        section(0, "1", "Inledning", 3, 4, "1 Inledning\n\nRamavtalet omfattar"),
        section(1, "2", "Omfattning", 6, 6, "2 Omfattning\n\nVid Avrop kan krav"),
    )

    findings = check(checked(CATALOGUE, sections, 6, (3, 5), "Kravkatalog"))

    assert [(f.section, f.message) for f in findings if f.section is not None] == [
        (
            0,
            "Avsnitt 1, s. 3-4, omfattar sidan 3 och följs av sidan 5 som saknar textlager "
            "(ingen OCR körs), så avsnittets text är ofullständig.",
        )
    ]


def test_a_scanned_file_without_sections_is_held_back_whole() -> None:
    amendment = checked(AMENDMENT_7, (), 10, tuple(range(1, 11)), "Volymavtal", IBM_PAGE)

    findings = check(amendment)

    assert findings == [
        Finding(
            check="missing_text",
            severity=Severity.QUARANTINE,
            subject=NO_SECTION,
            message=(
                "Dokumentet gav inget avsnitt: ingen av dess 10 sidor har textlager (ingen OCR "
                "körs), så det har inget att läsa in."
            ),
            sha256=AMENDMENT_7,
        ),
    ]  # no note on top: the quarantine already says why


def test_a_one_page_scan() -> None:
    findings = check(checked(AMENDMENT_5, (), 1, (1,), "Volymavtal", IBM_PAGE))

    assert [f.message for f in findings] == [
        "Dokumentet gav inget avsnitt: dess enda sida saknar textlager (ingen OCR körs), så det "
        "har inget att läsa in.",
    ]


def test_a_word_file_has_no_pages_to_lack_text() -> None:
    sections = (section(0, "1", "Inledning", None, None, "1 Inledning\n\nAvropsmall"),)

    assert check(checked(WORD, sections, 0, ())) == []


def test_a_file_without_sections_is_held_back_even_without_pages() -> None:
    findings = check(checked(WORD, (), 0, ()))

    assert [(f.severity, f.subject, f.message) for f in findings] == [
        (
            Severity.QUARANTINE,
            NO_SECTION,
            "Dokumentet gav ingen text att dela i avsnitt, så det har inget att läsa in.",
        )
    ]


def test_a_linked_file_that_was_not_parsed_is_held_back() -> None:
    parsed = checked(TERMS, TERMS_SECTIONS[1:2], 31, ())
    lost = (link(AMENDMENT_7, "Volymavtal", IBM_PAGE), link(AMENDMENT_7, "Volymavtal", IBM_PAGE))

    findings = check(parsed, links=lost)

    assert findings == [
        Finding(
            check="missing_text",
            severity=Severity.QUARANTINE,
            subject=NOT_PARSED,
            message=(
                'Filen som länken "Volymavtal" på sidan "Volymavtal för IBM" pekar på har inte '
                "tolkats (steg 2), så den är varken läst eller kontrollerad. Den länkas från 2 "
                "ställen."
            ),
            sha256=AMENDMENT_7,
            evidence="Volymavtal",
        )
    ]


def test_sections_with_the_same_name_are_told_apart_by_their_pages() -> None:
    sections = (
        section(1, None, "Tillgänglighet", 4, 4, "Tillgänglighet\n\nMed Tillgänglighet avses"),
        section(48, None, "Tillgänglighet", 10, 10, "Tillgänglighet\n\nVid Avrop kan krav"),
        section(49, None, "Utbildning", 10, 10, "Utbildning\n\nVid Avrop kan krav komma"),
    )

    findings = check(checked(CATALOGUE, sections, 11, (4, 10), title="Kravkatalog"))

    assert [(f.section, f.subject) for f in findings if f.section is not None] == [
        (1, "Tillgänglighet (s. 4)"),
        (48, "Tillgänglighet (s. 10)"),
        (49, "Utbildning"),
    ]
    assert len({f.key for f in findings}) == len(findings)
