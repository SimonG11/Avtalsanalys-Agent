"""Tests for the reference resolver: reference_resolver, resolve_in_document,
resolve_on_page and resolve_questions in avtalsagent.ingestion.extract.

The sentences, headings and link titles are from the pilot files on avropa.se
(2026-10-05), named by sha256 prefix and section next to each test. Made up for
the tests: the file ids ("allmanna-villkor"), the page URLs, the publication
dates where a test needs two documents to differ, the names in the questions
logs' headers ("Anna Berg" for the sender), and the made-up numbers "5.15.13" and
"fråga 99" that no file has.
"""

from datetime import date

import pytest

from avtalsagent.domain.documents import CatalogLink
from avtalsagent.domain.extracted import (
    DocumentMetadata,
    DocumentType,
    Reference,
    ReferenceKind,
    ReferenceMention,
    ReferenceStatus,
    ReferenceTarget,
)
from avtalsagent.domain.parsed import Section
from avtalsagent.ingestion.extract.reference_resolver import (
    CorpusDocument,
    counts_in_rate,
    resolution_rate,
    resolve,
)
from avtalsagent.ingestion.extract.resolve_questions import question_date

PAGE = "https://www.avropa.se/ramavtal/ramavtalsomraden/it-drift-mindre/"
OTHER_PAGE = "https://www.avropa.se/ramavtal/ramavtalsomraden/it-drift-storre/"
T = DocumentType
K = ReferenceKind
S = ReferenceStatus


def section(
    position: int, number: str | None, title: str, text: str = "", level: int | None = None
) -> Section:
    if level is None:
        level = number.count(".") + 1 if number else 1
    return Section(
        position=position,
        number=number,
        title=title,
        level=level,
        parent=None,
        path=(),
        page_start=None,
        page_end=None,
        text=text or f"{number or ''} {title}".strip(),
    )


def mention(
    text: str,
    raw: str,
    kind: ReferenceKind,
    key: str,
    rule: str,
    *,
    section: int = 0,
    document_name: str | None = None,
    status: ReferenceStatus | None = None,
) -> ReferenceMention:
    start = text.index(raw)
    return ReferenceMention(
        section=section,
        start=start,
        end=start + len(raw),
        raw=raw,
        kind=kind,
        key=key,
        rule=rule,
        document_name=document_name,
        status=status,
    )


def link(sha256: str, title: str, page: str = PAGE) -> CatalogLink:
    return CatalogLink(
        sha256=sha256,
        url=f"https://www.avropa.se/globalassets/{sha256}.pdf",
        title=title,
        category=None,
        agreement_number=None,
        site_updated=None,
        page_url=page,
        page_title="IT-drift",
        page_procurement_numbers=("23.3-5890-2023",),
        page_period=None,
    )


def document(
    sha256: str,
    title: str,
    document_type: DocumentType,
    sections: tuple[Section, ...] = (),
    mentions: tuple[ReferenceMention, ...] = (),
    *,
    pages: tuple[str, ...] = (PAGE,),
    published_on: date | None = None,
) -> CorpusDocument:
    metadata = DocumentMetadata(
        sha256=sha256,
        title=title,
        document_type=document_type,
        type_rule="test",
        agreement_number=None,
        annex_number=None,
        first_chapter=None,
        tendsign_cover=None,
        is_template=False,
        version_date=None,
        version_rule=None,
        published_on=published_on,
        site_updated=None,
    )
    links = tuple(link(sha256, title, page) for page in pages)
    return CorpusDocument(metadata, sections or (section(0, None, title),), mentions, links)


def resolved(*documents: CorpusDocument) -> Reference:
    """The reference of the first mention of the first document."""
    return resolve(documents)[0]


def target(sha256: str, section: int | None = None, page: str | None = None) -> ReferenceTarget:
    return ReferenceTarget(sha256=sha256, section=section, page_url=page)


# --- R1: section numbers in the same file ------------------------------------------------

# 21d3f9cdf880 §1.1.2.6, the "Exempelroller" of a requirements specification.
EXAMPLE_ROLE = "Erfarenhet av uppdrag enligt beskrivning i detta avsnitt ovan (se punkt 1.1.2.4)."


def test_r1_resolves_a_number_to_the_section_of_the_same_file() -> None:
    sections = (
        section(0, "1.1.2.4", "Arbetsuppgifter"),
        section(1, "1.1.2.6", "Krav", EXAMPLE_ROLE),
    )
    found = mention(EXAMPLE_ROLE, "punkt 1.1.2.4", K.SECTION_NUMBER, "1.1.2.4", "R1", section=1)
    file = document(
        "exempelroller", "Exempelroller", T.REQUIREMENTS_SPECIFICATION, sections, (found,)
    )

    reference = resolved(file)

    assert (reference.status, reference.rule) == (S.RESOLVED, "R1")
    assert reference.targets == (target("exempelroller", 0),)


def test_r1_a_number_the_file_lacks_is_missing_and_names_the_file_searched() -> None:
    # 54211e718d8e §9.1: an older numbering.
    text = "Kammarkollegiet kan begära uppgifter (se avsnitt 5.2 och 5.3)."
    found = mention(text, "avsnitt 5.2", K.SECTION_NUMBER, "5.2", "R1")
    file = document(
        "ansokan",
        "Ansökningsinbjudan",
        T.PROCUREMENT_DOCUMENT,
        (section(0, "9.1", "Allmänt", text),),
        (found,),
    )

    reference = resolved(file)

    assert (reference.status, reference.rule) == (S.NUMBER_MISSING, "R1")
    assert reference.targets == (target("ansokan"),)


def test_a_status_the_text_decided_is_kept_without_a_rule() -> None:
    # 997bef854b06 §1.16.3: the list items of the section, not sections.
    text = "till följd av uppsägning enligt punkterna 1-6 i detta avsnitt"
    item = mention(text, "punkterna 1", K.SECTION_NUMBER, "1", "R1", status=S.LIST_ITEM)
    file = document(
        "huvuddokument",
        "Ramavtalets huvuddokument",
        T.MAIN_DOCUMENT,
        (section(0, "1", "Inledning", text),),
        (item,),
    )

    reference = resolved(file)

    assert (reference.status, reference.rule, reference.targets) == (S.LIST_ITEM, None, ())


# --- R4: section titles ----------------------------------------------------------------

# 4f5a5c8becf6 §1.12.3.
PENALTIES = "kan det resultera i vite och andra påföljder enligt avsnitt Avtalsbrott och påföljder."


def title_reference(
    key: str, headings: list[tuple[str | None, str]], text: str = PENALTIES
) -> Reference:
    sections = tuple(section(i + 1, number, title) for i, (number, title) in enumerate(headings))
    sections = (section(0, "1.12.3", "Tillhandahållande", text), *sections)
    raw = text[text.index("avsnitt") :].rstrip(".")
    found = mention(text, raw, K.SECTION_TITLE, key, "R4")
    return resolved(document("ramavtal", "Ramavtal", T.SUPPLIER_AGREEMENT, sections, (found,)))


def test_r4_resolves_the_longest_heading_the_title_starts_with() -> None:
    reference = title_reference(
        "Avtalsbrott och påföljder",
        [("1.16", "Avtalsbrott"), ("1.17", "Avtalsbrott och påföljder")],
    )

    assert (reference.status, reference.rule) == (S.RESOLVED, "R4")
    assert reference.targets == (target("ramavtal", 2),)


def test_r4_a_heading_must_end_where_a_word_ends() -> None:
    # "bilaga Priser" names the price annex; the heading "Pris" is another section.
    text = "Priserna anges enligt avsnitt Priser."
    reference = title_reference("Priser", [("4.1", "Pris")], text)

    assert (reference.status, reference.rule) == (S.TITLE_MISSING, "R4")
    assert reference.targets == (target("ramavtal"),)


def test_r4_a_short_heading_counts() -> None:
    # 198d61824b3b §6.10, the Allmänna villkor's 6.20.2 "Fel".
    text = "inom angiven tid utgör avvikelserna Fel enligt avsnitt Fel."
    reference = title_reference("Fel", [("6.20.2", "Fel")], text)

    assert (reference.status, reference.targets) == (S.RESOLVED, (target("ramavtal", 1),))


def test_r4_a_heading_is_compared_without_its_final_dot() -> None:
    # 66b60a8f74a0 §5.8: the sentence's dot is not in the key, the heading's is in 5.4.3.
    heading = (
        "Uteslutningsgrunder som rör insolvens, intressekonflikter, "
        "allvarligt fel i yrkesutövning m.m"
    )
    text = f"Sanningsförsäkran för varje åberopat företag enligt avsnitt {heading}."
    sections = (section(0, "5.4.3", f"{heading}."), section(1, "5.8", "Sammanställning", text))
    found = mention(text, f"avsnitt {heading}", K.SECTION_TITLE, heading, "R4", section=1)
    file = document(
        "upphandling", "Upphandlingsdokument", T.PROCUREMENT_DOCUMENT, sections, (found,)
    )

    assert resolved(file).targets == (target("upphandling", 0),)


def test_r4_a_repeated_heading_is_the_one_in_the_references_own_chapter() -> None:
    # e1361d5fd2b0: "Vite vid avtalsbrott" ends each obligation; 9.17.1 means 9.17.4.
    text = "kan det resultera i vite och andra påföljder enligt avsnitt Vite vid avtalsbrott."
    sections = (
        section(0, "9.7.8", "Vite vid avtalsbrott"),
        section(1, "9.17.1", "Redovisning", text),
        section(2, "9.17.4", "Vite vid avtalsbrott"),
    )
    found = mention(
        text,
        "avsnitt Vite vid avtalsbrott",
        K.SECTION_TITLE,
        "Vite vid avtalsbrott",
        "R4",
        section=1,
    )

    reference = resolved(document("ramavtal", "Ramavtal", T.SUPPLIER_AGREEMENT, sections, (found,)))

    assert (reference.status, reference.targets) == (S.RESOLVED, (target("ramavtal", 2),))


def test_r4_a_repeated_heading_in_other_chapters_is_ambiguous() -> None:
    text = "enligt avsnitt Vite vid avtalsbrott."
    sections = (
        section(0, "9.7.8", "Vite vid avtalsbrott"),
        section(1, "1.2", "Inledning", text),
        section(2, "9.17.4", "Vite vid avtalsbrott"),
    )
    found = mention(
        text,
        "avsnitt Vite vid avtalsbrott",
        K.SECTION_TITLE,
        "Vite vid avtalsbrott",
        "R4",
        section=1,
    )

    reference = resolved(document("ramavtal", "Ramavtal", T.SUPPLIER_AGREEMENT, sections, (found,)))

    assert reference.status is S.AMBIGUOUS
    assert reference.targets == (target("ramavtal", 0), target("ramavtal", 2))


def test_r4_of_two_close_sections_with_one_heading_the_higher_level_wins() -> None:
    # f5823eb88227 §9.11.3: 9.11 and 9.11.2 are both "Information om Ramavtalet".
    text = "åtaganden enligt detta avsnitt Information om Ramavtalet, ska detta anses utgöra"
    sections = (
        section(0, "9.11", "Information om Ramavtalet"),
        section(1, "9.11.2", "Information om Ramavtalet"),
        section(2, "9.11.3", "Vite", text),
    )
    found = mention(
        text,
        "avsnitt Information om Ramavtalet",
        K.SECTION_TITLE,
        "Information om Ramavtalet, ska detta anses utgöra",
        "R4",
        section=2,
    )

    reference = resolved(document("ramavtal", "Ramavtal", T.SUPPLIER_AGREEMENT, sections, (found,)))

    assert reference.targets == (target("ramavtal", 0),)


def test_r4_a_section_of_the_annex_named_before_is_not_published() -> None:
    # 66b60a8f74a0 §4.5.2.2: a section of a tender form.
    text = '0 poäng för bilagans avsnitt "Kvalitet i utförandet hos kund - kunds bedömning"'
    found = mention(
        text, "avsnitt", K.SECTION_TITLE, "Kvalitet i utförandet hos kund - kunds bedömning", "R4"
    )
    file = document(
        "upphandling",
        "Upphandlingsdokument",
        T.PROCUREMENT_DOCUMENT,
        (section(0, "4.5.2.2", "Utvärdering", text),),
        (found,),
    )

    reference = resolved(file)

    assert (reference.status, reference.rule, reference.targets) == (S.NOT_PUBLISHED, "R4", ())


# 1d58dc2e8387 §3.1.1, in a "Redovisning av hållbarhetskrav".
SUSTAINABILITY = "Se Kravkatalog avsnitt Hållbarhet."


def report_with(text: str, key: str) -> CorpusDocument:
    raw = text[text.lower().index("avsnitt") :].rstrip(".:")
    found = mention(text, raw, K.SECTION_TITLE, key, "R4")
    sections = (section(0, "3.1.1", "Miljökrav på upphandlingsföremålet", text),)
    return document(
        "redovisning", "Redovisning av hållbarhetskrav", T.REQUIREMENTS_REPORT, sections, (found,)
    )


def test_r4_a_heading_only_another_file_of_the_page_has_resolves_there() -> None:
    catalogue = document(
        "kravkatalog", "Kravkatalog", T.REQUIREMENTS_CATALOGUE, (section(0, "8.1.3", "Hållbarhet"),)
    )

    reference = resolved(report_with(SUSTAINABILITY, "Hållbarhet"), catalogue)

    assert (reference.status, reference.rule) == (S.RESOLVED, "R4")
    assert reference.targets == (target("kravkatalog", 0),)


def test_r4_in_other_files_the_procurement_documents_count_last() -> None:
    # a61f1d5b1580 §2: the Kravkatalog's 7.7, which the tender documents repeat.
    text = "Avsnitt Personuppgiftsbehandling: Vid Avrop kan krav ställas"
    heading = (section(0, "7.7", "Personuppgiftsbehandling"),)
    catalogue = document("kravkatalog", "Kravkatalog", T.REQUIREMENTS_CATALOGUE, heading)
    tender = document("anbud", "Anbudsinbjudan", T.PROCUREMENT_DOCUMENT, heading)

    reference = resolved(report_with(text, "Personuppgiftsbehandling"), tender, catalogue)

    assert (reference.status, reference.targets) == (S.RESOLVED, (target("kravkatalog", 0),))


def test_r4_two_other_files_with_the_heading_are_ambiguous() -> None:
    catalogue = document(
        "kravkatalog", "Kravkatalog", T.REQUIREMENTS_CATALOGUE, (section(0, "8.1.3", "Hållbarhet"),)
    )
    terms = document(
        "villkor", "Allmänna villkor", T.GENERAL_TERMS, (section(0, "6.4", "Hållbarhet"),)
    )

    reference = resolved(report_with(SUSTAINABILITY, "Hållbarhet"), catalogue, terms)

    assert reference.status is S.AMBIGUOUS
    assert set(reference.targets) == {target("kravkatalog", 0), target("villkor", 0)}


def test_r4_in_other_files_the_longest_heading_wins() -> None:
    # ddf8f04f4ab4 §4.2: "Betalning av skatter", not the general terms' "Betalning".
    text = "enligt avsnitt Betalning av skatter och avsnitt Betalning av socialförsäkringsavgifter"
    terms = document(
        "villkor", "Allmänna villkor", T.GENERAL_TERMS, (section(0, "6.13.3", "Betalning"),)
    )
    catalogue = document(
        "kravkatalog",
        "Kravkatalog",
        T.REQUIREMENTS_CATALOGUE,
        (section(0, "3.2.2.1", "Betalning av skatter"),),
    )

    reference = resolved(report_with(text, "Betalning av skatter"), terms, catalogue)

    assert reference.targets == (target("kravkatalog", 0),)


def test_r4_a_shared_file_gets_one_target_per_page_when_the_pages_differ() -> None:
    report = report_with(SUSTAINABILITY, "Hållbarhet")
    report = CorpusDocument(
        report.metadata,
        report.sections,
        report.mentions,
        (link("redovisning", "Redovisning", PAGE), link("redovisning", "Redovisning", OTHER_PAGE)),
    )
    heading = (section(0, "8.1.3", "Hållbarhet"),)
    small = document("kravkatalog-mindre", "Kravkatalog", T.REQUIREMENTS_CATALOGUE, heading)
    large = document(
        "kravkatalog-storre", "Kravkatalog", T.REQUIREMENTS_CATALOGUE, heading, pages=(OTHER_PAGE,)
    )

    reference = resolved(report, small, large)

    assert reference.status is S.RESOLVED
    assert reference.targets == (
        target("kravkatalog-mindre", 0, PAGE),
        target("kravkatalog-storre", 0, OTHER_PAGE),
    )


# --- R2: named documents ---------------------------------------------------------------


def document_reference(
    text: str,
    raw: str,
    key: str,
    referring_type: DocumentType = T.MAIN_DOCUMENT,
    *others: CorpusDocument,
    referring_title: str = "Ramavtalets huvuddokument",
    sections: tuple[Section, ...] = (),
) -> Reference:
    found = mention(text, raw, K.DOCUMENT, key, "R2")
    sections = sections or (section(0, "5.18", "Ramavtalets upphörande", text),)
    referring = document("referring", referring_title, referring_type, sections, (found,))
    return resolved(referring, *others)


TERMINATION = "I Allmänna villkor finns dock en rätt för Avropsberättigad att säga upp Kontrakt."


def test_r2_resolves_a_document_name_to_the_link_with_that_title() -> None:
    terms = document("villkor", "Allmänna villkor", T.GENERAL_TERMS)

    reference = document_reference(
        TERMINATION, "Allmänna villkor", "allmänna villkor", T.MAIN_DOCUMENT, terms
    )

    assert (reference.status, reference.rule, reference.targets) == (
        S.RESOLVED,
        "R2",
        (target("villkor"),),
    )


def test_r2_a_document_the_page_does_not_list_is_not_published() -> None:
    reference = document_reference(TERMINATION, "Allmänna villkor", "allmänna villkor")

    assert (reference.status, reference.rule, reference.targets) == (S.NOT_PUBLISHED, "R2", ())


def test_r2_an_alias_finds_the_link_title_the_name_stands_for() -> None:
    text = "enligt Huvuddokumentet gäller följande"
    main = document("huvuddokument", "Ramavtalets huvuddokument", T.MAIN_DOCUMENT)

    reference = document_reference(
        text,
        "Huvuddokumentet",
        "huvuddokument",
        T.GENERAL_TERMS,
        main,
        referring_title="Allmänna villkor",
    )

    assert reference.targets == (target("huvuddokument"),)


def test_r2_the_files_own_name_is_self() -> None:
    # ee1d917d9eb3 §2.11: the Allmänna villkor naming itself.
    text = "Specifika delar om proprietär programvara i Allmänna villkor, får där så särskilt anges"

    reference = document_reference(
        text,
        "Allmänna villkor",
        "allmänna villkor",
        T.GENERAL_TERMS,
        referring_title="Allmänna villkor",
    )

    assert (reference.status, reference.rule) == (S.SELF, "R2")


def test_r2_the_files_own_name_is_self_also_when_it_has_a_chapter_of_that_name() -> None:
    # 0a5491b1398e §6.2: the Allmänna villkor are chapter 6 "Allmänna villkor" of the file.
    text = "utgör en bilaga till Kontraktet oavsett om Allmänna villkor åberopas eller inte."
    sections = (section(0, "6", "Allmänna villkor"), section(1, "6.2", "Allmänt", text))

    reference = document_reference(
        text,
        "Allmänna villkor",
        "allmänna villkor",
        T.GENERAL_TERMS,
        referring_title="Allmänna villkor",
        sections=sections,
    )

    assert (reference.status, reference.rule) == (S.SELF, "R2")


def test_r2_a_supplier_card_is_its_own_main_document() -> None:
    # 7a49e1a61b31 §9.2.1, a supplier's "Ramavtal".
    text = "3. Ramavtalets Huvuddokument med bilagor"
    main = document("huvuddokument", "Ramavtalets huvuddokument", T.MAIN_DOCUMENT)

    card = document_reference(
        text,
        "Ramavtalets Huvuddokument",
        "ramavtalets huvuddokument",
        T.SUPPLIER_AGREEMENT,
        main,
        referring_title="Ramavtal",
    )
    terms = document_reference(
        text,
        "Ramavtalets Huvuddokument",
        "ramavtalets huvuddokument",
        T.GENERAL_TERMS,
        main,
        referring_title="Allmänna villkor",
    )

    assert (card.status, card.rule) == (S.SELF, "R2")
    assert terms.targets == (target("huvuddokument"),)


def test_r2_a_top_level_chapter_of_that_name_in_the_file_comes_first() -> None:
    # 11db2f3d1852 §5.18: the tender document holds the Allmänna villkor as chapter 6.
    chapters = (
        section(0, "5.18", "Ramavtalets upphörande", TERMINATION),
        section(1, "6", "Allmänna Villkor"),
    )
    terms = document("villkor", "Allmänna villkor", T.GENERAL_TERMS)

    reference = document_reference(
        TERMINATION,
        "Allmänna villkor",
        "allmänna villkor",
        T.PROCUREMENT_DOCUMENT,
        terms,
        referring_title="Upphandlingsdokument",
        sections=chapters,
    )

    assert (reference.status, reference.targets) == (S.RESOLVED, (target("referring", 1),))


def test_r2_a_chapter_is_found_by_an_alias_of_the_name() -> None:
    # 11db2f3d1852 §5.4: its chapter 5 "Ramavtalets Huvuddokument".
    text = "Med Part avses i Huvuddokument Kammarkollegiet eller Ramavtalsleverantören."
    chapters = (
        section(0, "5", "Ramavtalets Huvuddokument"),
        section(1, "5.4", "Definitioner", text),
    )
    main = document("huvuddokument", "Ramavtalets huvuddokument", T.MAIN_DOCUMENT)

    reference = document_reference(
        text,
        "Huvuddokument",
        "huvuddokument",
        T.PROCUREMENT_DOCUMENT,
        main,
        referring_title="Upphandlingsdokument",
        sections=chapters,
    )

    assert (reference.status, reference.targets) == (S.RESOLVED, (target("referring", 0),))


def test_r2_a_lower_section_of_that_name_is_no_chapter() -> None:
    sections = (
        section(0, "5.18", "Ramavtalets upphörande", TERMINATION),
        section(1, "6.1", "Allmänna villkor"),
    )
    terms = document("villkor", "Allmänna villkor", T.GENERAL_TERMS)

    reference = document_reference(
        TERMINATION,
        "Allmänna villkor",
        "allmänna villkor",
        T.PROCUREMENT_DOCUMENT,
        terms,
        referring_title="Upphandlingsdokument",
        sections=sections,
    )

    assert reference.targets == (target("villkor"),)


def test_r2_a_family_of_files_on_one_page_is_ambiguous() -> None:
    # 198d61824b3b §6.19: three levels of Säkerhetsskyddsavtal on every page.
    text = "kan framgå av eventuellt Säkerhetsskyddsavtal samt personuppgiftsbiträdesavtal"
    levels = [
        document(f"ssa-{n}", f"Utkast till Säkerhetsskyddsavtal (Nivå {n})", T.TEMPLATE)
        for n in (1, 2, 3)
    ]

    reference = document_reference(
        text,
        "Säkerhetsskyddsavtal",
        "säkerhetsskyddsavtal",
        T.GENERAL_TERMS,
        *levels,
        referring_title="Allmänna villkor",
    )

    assert (reference.status, reference.rule) == (S.AMBIGUOUS, "R2")
    assert reference.targets == (target("ssa-1"), target("ssa-2"), target("ssa-3"))


PACKAGE = "3. Upphandlingsdokumenten med bilagor inklusive rättelser"  # 80578a77ea47 §5.5


def test_r2_the_tender_package_is_every_procurement_file_of_the_page() -> None:
    tender = document("upphandling", "Upphandlingsdokument", T.PROCUREMENT_DOCUMENT)
    log = document("fragor", "Frågor och svar - Upphandlingsdokument", T.QUESTIONS_AND_ANSWERS)

    reference = document_reference(
        PACKAGE, "Upphandlingsdokumenten", "upphandlingsdokument", T.MAIN_DOCUMENT, tender, log
    )

    assert reference.status is S.AMBIGUOUS
    assert reference.targets == (target("upphandling"), target("fragor"))


def test_r2_the_singular_names_one_tender_document() -> None:
    text = "3. Upphandlingsdokumentet med bilagor inklusive rättelser"
    tender = document("upphandling", "Upphandlingsdokument", T.PROCUREMENT_DOCUMENT)
    log = document("fragor", "Frågor och svar - Upphandlingsdokument", T.QUESTIONS_AND_ANSWERS)

    reference = document_reference(
        text, "Upphandlingsdokumentet", "upphandlingsdokument", T.MAIN_DOCUMENT, tender, log
    )

    assert (reference.status, reference.targets) == (S.RESOLVED, (target("upphandling"),))


def test_r2_the_tender_package_named_in_a_log_leaves_the_log_out() -> None:
    # 39d8c1efe373 §pos1.
    body = "Sista dag att ställa frågor om upphandlingsdokumenten är 2025-02-26."
    log = log_with(body, "upphandlingsdokumenten", K.DOCUMENT, "upphandlingsdokument", "R2")
    offer = document("anbud", "Anbudsinbjudan", T.PROCUREMENT_DOCUMENT)

    reference = resolved(log, offer)

    assert (reference.status, reference.targets) == (S.RESOLVED, (target("anbud"),))


# --- R1x: a number with a document named -------------------------------------------------


def named_number(
    text: str,
    raw: str,
    number: str,
    name: str,
    *others: CorpusDocument,
    referring_type: DocumentType = T.CALL_OFF_GUIDANCE,
    title: str = "Vägledning",
) -> Reference:
    found = mention(text, raw, K.SECTION_NUMBER, number, "R1x", document_name=name)
    referring = document(
        "referring", title, referring_type, (section(0, "2.7", "Valuta", text),), (found,)
    )
    return resolved(referring, *others)


# 49f36699a469 §2.7.
CURRENCY = "I Allmänna villkor punkt 6.17 finns en valutaklausul angiven om inget annat är avtalat."


def test_r1x_resolves_the_number_in_the_named_document() -> None:
    terms = document(
        "villkor", "Allmänna villkor", T.GENERAL_TERMS, (section(0, "6.17", "Valuta"),)
    )

    reference = named_number(CURRENCY, "punkt 6.17", "6.17", "allmänna villkor", terms)

    assert (reference.status, reference.rule, reference.targets) == (
        S.RESOLVED,
        "R1x",
        (target("villkor", 0),),
    )


def test_r1x_a_number_the_named_document_lacks_is_missing_there() -> None:
    terms = document(
        "villkor", "Allmänna villkor", T.GENERAL_TERMS, (section(0, "6.18", "Valuta"),)
    )

    reference = named_number(CURRENCY, "punkt 6.17", "6.17", "allmänna villkor", terms)

    assert (reference.status, reference.targets) == (S.NUMBER_MISSING, (target("villkor"),))


def test_r1x_a_document_the_page_does_not_list_is_not_published() -> None:
    reference = named_number(CURRENCY, "punkt 6.17", "6.17", "allmänna villkor")

    assert (reference.status, reference.rule) == (S.NOT_PUBLISHED, "R1x")


def test_r1x_the_named_document_as_a_chapter_of_a_tender_document() -> None:
    # bdf58b81d100 §42: the published Allmänna villkor is numbered 2.x.
    text = "p. 6.19.7 i Allmänna villkor - Anbudsgivaren önskar ett förtydligande"
    terms = document(
        "villkor", "Allmänna villkor", T.GENERAL_TERMS, (section(0, "2.1", "Allmänt"),)
    )
    chapters = (
        section(0, "6", "Utkast Allmänna villkor"),
        section(1, "6.19.7", "Begränsning av vite"),
    )
    tender = document("ansokan", "Ansökningsinbjudan", T.PROCUREMENT_DOCUMENT, chapters)

    reference = named_number(text, "p. 6.19.7", "6.19.7", "allmänna villkor", terms, tender)

    assert (reference.status, reference.targets) == (S.RESOLVED, (target("ansokan", 1),))


def test_r1x_a_tender_document_counts_only_with_the_number_in_that_chapter() -> None:
    # 087f9c5a2f56 §44: the tender documents' 2 is not in their chapter 6.
    text = "Beträffande 6.16.3, punkt 2 i ramavtalets huvuddokument:"
    main = document(
        "huvuddokument",
        "Ramavtalets huvuddokument",
        T.MAIN_DOCUMENT,
        (section(0, "6.16.3", "Vite"),),
    )
    chapters = (
        section(0, "2", "Administrativa förutsättningar"),
        section(1, "6", "Utkast Ramavtalets huvuddokument"),
    )
    tender = document("upphandling", "Upphandlingsdokument", T.PROCUREMENT_DOCUMENT, chapters)

    reference = named_number(text, "punkt 2", "2", "ramavtalets huvuddokument", main, tender)

    assert (reference.status, reference.targets) == (S.NUMBER_MISSING, (target("huvuddokument"),))


def test_r1x_the_named_document_comes_before_a_tender_chapter() -> None:
    # 20c753d88340 §81: the published Allmänna villkor's 6.9, not the tender's chapter 6.
    text = "Allmänna villkor, punkt 6.9 sista stycke finns en skadeslöshetsförbindelse."
    terms = document(
        "villkor",
        "Allmänna villkor",
        T.GENERAL_TERMS,
        (section(0, "6.9", "Immateriella rättigheter"),),
    )
    chapters = (section(0, "6", "Allmänna villkor"), section(1, "6.9", "Immateriella rättigheter"))
    tender = document("upphandling", "Upphandlingsdokument", T.PROCUREMENT_DOCUMENT, chapters)

    reference = named_number(text, "punkt 6.9", "6.9", "allmänna villkor", tender, terms)

    assert (reference.status, reference.targets) == (S.RESOLVED, (target("villkor", 0),))


def test_r1x_a_chapter_of_the_file_itself_comes_first() -> None:
    # 54211e718d8e §2.5: the Ansökningsinbjudan's chapter 10 is the draft main document.
    text = "Se även Ramavtalets huvuddokument kapitel 10 för vad som gäller för Åberopade företag"
    found = mention(
        text,
        "kapitel 10",
        K.SECTION_NUMBER,
        "10",
        "R1x",
        document_name="ramavtalets huvuddokument",
    )
    sections = (
        section(0, "2.5", "Åberopade företag", text),
        section(1, "10", "Utkast Ramavtalets Huvuddokument"),
    )
    application = document(
        "ansokan", "Ansökningsinbjudan", T.PROCUREMENT_DOCUMENT, sections, (found,)
    )
    main = document(
        "huvuddokument",
        "Ramavtalets huvuddokument",
        T.MAIN_DOCUMENT,
        (section(0, "1", "Inledning"),),
    )

    reference = resolved(application, main)

    assert (reference.status, reference.targets) == (S.RESOLVED, (target("ansokan", 1),))


def test_r1x_the_file_naming_itself_looks_in_itself() -> None:
    text = "Se Upphandlingsdokument avsnitt 5.9.9.1 Antidiskriminering."
    found = mention(
        text,
        "avsnitt 5.9.9.1",
        K.SECTION_NUMBER,
        "5.9.9.1",
        "R1x",
        document_name="upphandlingsdokument",
    )
    sections = (
        section(0, "2.4.2", "Social hänsyn", text),
        section(1, "5.9.9.1", "Antidiskriminering"),
    )
    tender = document(
        "upphandling", "Upphandlingsdokument", T.PROCUREMENT_DOCUMENT, sections, (found,)
    )

    reference = resolved(tender)

    assert (reference.status, reference.targets) == (S.RESOLVED, (target("upphandling", 1),))


# --- R3: annex numbers -----------------------------------------------------------------

# 171a3cacf5fd §10.1, the Microsoft volume agreement's list of annexes.
ANNEXES = "Bilaga 5 Tillägg och förtydliganden till bilagorna 5.1-5.4 o Bilaga 5.1 Enterprise-avtal"


def annex_number(
    number: str, referring_type: DocumentType = T.MAIN_DOCUMENT, *others: CorpusDocument
) -> Reference:
    raw = f"Bilaga {number} "
    found = mention(ANNEXES, raw.strip(), K.ANNEX_NUMBER, number, "R3")
    referring = document(
        "volymavtal",
        "Volymavtalets huvudavtal",
        referring_type,
        (section(0, "10.1", "Bilagor", ANNEXES),),
        (found,),
    )
    return resolved(referring, *others)


def test_r3_resolves_an_annex_number_to_the_link_with_that_number() -> None:
    enterprise = document("enterprise", "Bilaga 5.1 Enterprise-avtal", T.LICENCE_TERMS)

    reference = annex_number("5.1", T.MAIN_DOCUMENT, enterprise)

    assert (reference.status, reference.rule, reference.targets) == (
        S.RESOLVED,
        "R3",
        (target("enterprise"),),
    )


def test_r3_the_number_must_be_the_whole_annex_number() -> None:
    enterprise = document("enterprise", "Bilaga 5.1 Enterprise-avtal", T.LICENCE_TERMS)

    reference = annex_number("5", T.MAIN_DOCUMENT, enterprise)

    assert (reference.status, reference.rule) == (S.NOT_PUBLISHED, "R3")


def test_r3_licence_terms_number_their_own_annexes() -> None:
    # e0db1184576c §pos8: 'Med "GDPR-villkor" avses villkoren i Bilaga 1'.
    contacts = document("kontakt", "Bilaga 1 Kontaktuppgifter", T.ANNEX)
    text = 'Med "GDPR-villkor" avses villkoren i Bilaga 1'
    found = mention(text, "Bilaga 1", K.ANNEX_NUMBER, "1", "R3")
    dpa = document(
        "dpa",
        "Bilaga 10.1 Villkor för Dataskyddstillägg",
        T.LICENCE_TERMS,
        (section(0, None, "DPA", text, 0),),
        (found,),
    )

    reference = resolved(dpa, contacts)

    assert (reference.status, reference.targets) == (S.NOT_PUBLISHED, ())


# --- R5: annex names -------------------------------------------------------------------


def annex_name(
    text: str,
    key: str,
    *others: CorpusDocument,
    name: str | None = None,
    referring: str = "referring",
) -> Reference:
    raw = text[text.lower().index("bilaga") :].rstrip(".")
    found = mention(text, raw, K.ANNEX_NAME, key, "R5", document_name=name)
    file = document(
        referring,
        "Upphandlingsdokument",
        T.PROCUREMENT_DOCUMENT,
        (section(0, "2.6", "Datadelning", text),),
        (found,),
    )
    return resolved(file, *others)


def test_r5_resolves_an_annex_name_by_the_link_title_it_starts_with() -> None:
    # 11db2f3d1852 §2.6.
    text = "Anbudsgivaren ska acceptera bilaga Utkast till Datadelningsavtal. Det innebär"
    data = document("datadelning", "Utkast till datadelningsavtal", T.TEMPLATE)

    reference = annex_name(text, "Utkast till Datadelningsavtal", data, name="datadelningsavtal")

    assert (reference.status, reference.rule, reference.targets) == (
        S.RESOLVED,
        "R5",
        (target("datadelning"),),
    )


def test_r5_an_annex_the_page_does_not_publish_is_not_published() -> None:
    # 264aff0ce61a §9.1.2: a tender form.
    text = "Av bilaga Avropsberättigade framgår vilka myndigheter som får avropa."
    terms = document("villkor", "Allmänna villkor", T.GENERAL_TERMS)

    reference = annex_name(
        text, "Avropsberättigade framgår vilka myndigheter som får avropa", terms
    )

    assert (reference.status, reference.rule) == (S.NOT_PUBLISHED, "R5")


def test_r5_the_longest_link_title_wins() -> None:
    text = "enligt bilaga Avropsrutin och Kravkatalog."
    routine = document("rutin", "Avropsrutin", T.ANNEX)
    both = document("rutin-och-katalog", "Avropsrutin och Kravkatalog", T.ANNEX)

    reference = annex_name(text, "Avropsrutin och Kravkatalog", routine, both)

    assert reference.targets == (target("rutin-och-katalog"),)


def test_r5_an_alias_finds_the_price_annex() -> None:
    # 34d71a7e4da0 §8.10.1.
    text = "Ramavtalsleverantörens priser för Upphandlingsföremålet anges i bilaga Priser."
    prices = document("prisbilaga", "Prisbilaga - sammanställning Delområde 1", T.PRICE_ANNEX)

    reference = annex_name(text, "Priser", prices)

    assert (reference.status, reference.targets) == (S.RESOLVED, (target("prisbilaga"),))


def test_r5_the_document_the_name_starts_with_is_found_as_by_r2() -> None:
    # 34d71a7e4da0 §8.4: the name starts the link title, not the other way round.
    text = "· Bilaga Avropsberättigade\n\n· Bilaga Avropsrutin\n\n· Bilaga Priser"
    found = mention(
        text, "Bilaga Avropsrutin", K.ANNEX_NAME, "Avropsrutin", "R5", document_name="avropsrutin"
    )
    main = document(
        "huvuddokument",
        "Ramavtalets huvuddokument",
        T.MAIN_DOCUMENT,
        (section(0, "8.4", "Ramavtalets handlingar", text),),
        (found,),
    )
    routine = document("rutin-och-katalog", "Avropsrutin och Kravkatalog", T.ANNEX)

    reference = resolved(main, routine)

    assert (reference.status, reference.targets) == (S.RESOLVED, (target("rutin-och-katalog"),))


def test_r5_a_word_broken_over_two_lines_still_matches() -> None:
    # 0486216326ec §pos0: the cover of the Personuppgiftsbiträdesavtal itself.
    text = "Bilaga Utkast till personuppgiftsbiträdes-avtal Programvaror och tjänster"
    found = mention(
        text,
        "Bilaga Utkast till personuppgiftsbiträdes-avtal",
        K.ANNEX_NAME,
        "Utkast till personuppgiftsbiträdes-avtal Programvaror och tjänster",
        "R5",
    )
    file = document(
        "pub",
        "Utkast till Personuppgiftsbiträdesavtal",
        T.TEMPLATE,
        (section(0, None, "Text", text, 0),),
        (found,),
    )

    reference = resolved(file)

    assert (reference.status, reference.rule) == (S.SELF, "R5")


# --- Questions-and-answers logs --------------------------------------------------------

# A TendSign log entry; the dates are those of bdf58b81d100 §122 (answer before question).
QUESTION = (
    "Publik fråga 82\n\nFrån:\n\nDold Datum:\n\nTill:\n\n2024-06-04 10:08\n\nAlla\n\n"
    "2024-05-30 16:14\n\nAlla\n\n{body}\n\nPublikt svar\n\nFrån:\n\nAnna Berg"
)


def log_with(
    body: str,
    raw: str,
    kind: ReferenceKind,
    key: str,
    rule: str,
    *,
    title: str = "Frågor och svar - Upphandlingsdokument",
    document_name: str | None = None,
) -> CorpusDocument:
    text = QUESTION.format(body=body)
    found = mention(text, raw, kind, key, rule, section=1, document_name=document_name)
    sections = (section(0, "1", "Publik fråga"), section(1, "82", "Publik fråga", text))
    return document("fragor", title, T.QUESTIONS_AND_ANSWERS, sections, (found,))


# 39d8c1efe373 §82.
UTBYTE = "en väg vara att i avsnitt 7.8.1 Utbyte av konsult, tredje stycket, lägga till"


def tender(sha256: str, title: str, published_on: date | None, *numbers: str) -> CorpusDocument:
    sections = tuple(section(i, number, "Utbyte av Konsult") for i, number in enumerate(numbers))
    return document(sha256, title, T.PROCUREMENT_DOCUMENT, sections, published_on=published_on)


def test_question_date_is_the_earliest_time_stamp_of_the_entry() -> None:
    text = QUESTION.format(body="anbud ska lämnas senast 2024-05-01")

    assert question_date(text) == date(2024, 5, 30)
    assert question_date("anbud ska lämnas senast 2024-05-01") is None
    assert question_date("Datum: 2024-02-30 10:00") is None  # no such day


def test_rq_resolves_a_question_number_to_that_question_of_the_log() -> None:
    # 39d8c1efe373 §7.
    body = "Följdfråga på fråga 1 avseende 5.6.3.1 Kvalitetsledningssystem"
    log = log_with(body, "fråga 1", K.QUESTION, "1", "RQ")

    reference = resolved(log)

    assert (reference.status, reference.rule, reference.targets) == (
        S.RESOLVED,
        "RQ",
        (target("fragor", 0),),
    )


def test_rq_a_question_the_log_lacks_is_missing() -> None:
    log = log_with("Följdfråga på fråga 99 avseende villkoren", "fråga 99", K.QUESTION, "99", "RQ")

    assert resolved(log).status is S.NUMBER_MISSING


def test_r1q_a_number_in_a_log_resolves_in_the_procurement_document() -> None:
    log = log_with(UTBYTE, "avsnitt 7.8.1", K.SECTION_NUMBER, "7.8.1", "R1")

    reference = resolved(log, tender("anbud", "Anbudsinbjudan", None, "7.8.1"))

    assert (reference.status, reference.rule, reference.targets) == (
        S.RESOLVED,
        "R1q",
        (target("anbud", 0),),
    )


def test_r1q_a_number_no_procurement_document_has_is_missing_there() -> None:
    log = log_with(UTBYTE, "avsnitt 7.8.1", K.SECTION_NUMBER, "7.8.1", "R1")

    reference = resolved(log, tender("anbud", "Anbudsinbjudan", None, "7.8.2"))

    assert (reference.status, reference.rule, reference.targets) == (
        S.NUMBER_MISSING,
        "R1q",
        (target("anbud"),),
    )


def test_r1q_the_document_the_logs_title_names_comes_first() -> None:
    log = log_with(
        UTBYTE,
        "avsnitt 7.8.1",
        K.SECTION_NUMBER,
        "7.8.1",
        "R1",
        title="Frågor och svar - Ansökningsinbjudan",
    )
    application = tender("ansokan", "Ansökningsinbjudan", date(2024, 9, 1), "7.8.1")
    offer = tender("anbud", "Anbudsinbjudan", date(2024, 5, 2), "7.8.1")

    reference = resolved(log, application, offer)

    assert reference.targets == (target("ansokan", 0),)


@pytest.mark.parametrize(
    ("application_on", "offer_on", "expected"),
    [
        # Both out before the question (2024-05-30): the later one.
        (date(2023, 9, 4), date(2024, 5, 2), "anbud"),
        # The Anbudsinbjudan came after the question: the Ansökningsinbjudan.
        (date(2024, 5, 2), date(2024, 9, 5), "ansokan"),
    ],
)
def test_r1q_the_question_date_picks_among_procurement_documents(
    application_on: date, offer_on: date, expected: str
) -> None:
    log = log_with(UTBYTE, "avsnitt 7.8.1", K.SECTION_NUMBER, "7.8.1", "R1")
    application = tender("ansokan", "Ansökningsinbjudan", application_on, "7.8.1")
    offer = tender("anbud", "Anbudsinbjudan", offer_on, "7.8.1")

    reference = resolved(log, application, offer)

    assert (reference.status, reference.rule) == (S.RESOLVED, "R1q")
    assert reference.targets == (target(expected, 0),)


def test_r1q_a_later_version_with_no_first_date_leaves_it_ambiguous() -> None:
    # b2bf8baefe48 says "Version 3: publicerad 2024-11-19": published_on is None.
    log = log_with(UTBYTE, "avsnitt 7.8.1", K.SECTION_NUMBER, "7.8.1", "R1")
    application = tender("ansokan", "Ansökningsinbjudan", date(2024, 5, 2), "7.8.1")
    offer = tender("anbud", "Anbudsinbjudan", None, "7.8.1")

    reference = resolved(log, application, offer)

    assert (reference.status, reference.rule) == (S.AMBIGUOUS, "R1q")
    assert reference.targets == (target("ansokan", 0), target("anbud", 0))


def test_outside_a_log_a_number_stays_in_the_file() -> None:
    text = UTBYTE
    found = mention(text, "avsnitt 7.8.1", K.SECTION_NUMBER, "7.8.1", "R1")
    guide = document(
        "vagledning",
        "Vägledning",
        T.CALL_OFF_GUIDANCE,
        (section(0, "1", "Inledning", text),),
        (found,),
    )

    reference = resolved(guide, tender("anbud", "Anbudsinbjudan", None, "7.8.1"))

    assert (reference.status, reference.rule) == (S.NUMBER_MISSING, "R1")


def test_r1x_in_a_log_is_settled_by_the_question_date() -> None:
    # bdf58b81d100 §159: "p. 6.19.7 i Upphandlingsdokument" names both tender documents.
    body = "Hej! p. 6.19.7 i Upphandlingsdokument - följdfråga 79"
    log = log_with(
        body, "p. 6.19.7", K.SECTION_NUMBER, "6.19.7", "R1x", document_name="upphandlingsdokument"
    )
    application = tender("ansokan", "Ansökningsinbjudan", date(2024, 5, 2), "6.19.7")
    offer = tender("anbud", "Anbudsinbjudan", date(2024, 9, 5), "6.19.7")

    reference = resolved(log, application, offer)

    assert (reference.status, reference.rule, reference.targets) == (
        S.RESOLVED,
        "R1x",
        (target("ansokan", 0),),
    )


def test_the_date_does_not_choose_among_a_log_and_tender_documents() -> None:
    # 20ddb9ebf9cc §18: the whole tender package, the other phase's log with it.
    body = "I Punkt 2.8.1 Frågor och svar om upphandlingsdokumenten är angivet datum fel."
    log = log_with(body, "upphandlingsdokumenten", K.DOCUMENT, "upphandlingsdokument", "R2")
    tender_files = (
        document(
            "ansokan", "Ansökningsinbjudan", T.PROCUREMENT_DOCUMENT, published_on=date(2023, 9, 4)
        ),
        document(
            "fragor-ansokan",
            "Frågor och svar - Ansökningsinbjudan",
            T.QUESTIONS_AND_ANSWERS,
            published_on=date(2023, 9, 8),
        ),
        document(
            "upphandling",
            "Upphandlingsdokument",
            T.PROCUREMENT_DOCUMENT,
            published_on=date(2024, 2, 6),
        ),
    )

    reference = resolved(log, *tender_files)

    assert (reference.status, len(reference.targets)) == (S.AMBIGUOUS, 3)


def test_the_date_chooses_only_in_a_log() -> None:
    # The entry of bdf58b81d100 §159 copied into a guidance document (made up).
    text = QUESTION.format(body="Hej! p. 6.19.7 i Upphandlingsdokument - följdfråga 79")
    application = tender("ansokan", "Ansökningsinbjudan", date(2024, 5, 2), "6.19.7")
    offer = tender("anbud", "Anbudsinbjudan", date(2024, 4, 5), "6.19.7")

    reference = named_number(
        text, "p. 6.19.7", "6.19.7", "upphandlingsdokument", application, offer
    )

    assert (reference.status, reference.rule) == (S.AMBIGUOUS, "R1x")


def test_r4q_a_title_in_a_log_resolves_in_the_procurement_document() -> None:
    # bdf58b81d100 §24.
    body = "relevant kompetens enligt avsnitt Upphandlingsföremål och avsnitt Exempel"
    log = log_with(
        body, "avsnitt Upphandlingsföremål", K.SECTION_TITLE, "Upphandlingsföremål", "R4"
    )
    application = document(
        "ansokan",
        "Ansökningsinbjudan",
        T.PROCUREMENT_DOCUMENT,
        (section(0, "1.6", "Upphandlingsföremål"),),
    )

    reference = resolved(log, application)

    assert (reference.status, reference.rule, reference.targets) == (
        S.RESOLVED,
        "R4q",
        (target("ansokan", 0),),
    )


def test_a_title_missing_in_a_log_names_the_procurement_document_searched() -> None:
    body = "enligt avsnitt Försäljningsredovisning och administrativ avgift"
    log = log_with(
        body,
        "avsnitt Försäljningsredovisning",
        K.SECTION_TITLE,
        "Försäljningsredovisning och administrativ avgift",
        "R4",
    )
    application = document(
        "ansokan",
        "Ansökningsinbjudan",
        T.PROCUREMENT_DOCUMENT,
        (section(0, "1.6", "Upphandlingsföremål"),),
    )

    reference = resolved(log, application)

    assert (reference.status, reference.rule, reference.targets) == (
        S.TITLE_MISSING,
        "R4",
        (target("ansokan"),),
    )


# --- The rate --------------------------------------------------------------------------


def reference(kind: ReferenceKind, status: ReferenceStatus) -> Reference:
    found = ReferenceMention(section=0, start=0, end=1, raw="x", kind=kind, key="x", rule="R1")
    return Reference(sha256="a", mention=found, status=status, rule=None)


def test_the_rate_leaves_out_laws_questions_list_items_and_self() -> None:
    references = [
        reference(K.SECTION_NUMBER, S.RESOLVED),
        reference(K.ANNEX_NAME, S.NOT_PUBLISHED),  # counted: the page lacks the file
        reference(K.SECTION_NUMBER, S.LIST_ITEM),
        reference(K.DOCUMENT, S.SELF),
        reference(K.LAW, S.EXTERNAL),
        reference(K.QUESTION, S.RESOLVED),
    ]

    assert [counts_in_rate(r) for r in references] == [True, True, False, False, False, False]
    assert resolution_rate(references) == 0.5
    assert resolution_rate([]) == 0.0


def test_resolve_gives_one_reference_per_mention_in_order() -> None:
    text = "enligt punkt 1.1 och punkt 9.9"
    first = mention(text, "punkt 1.1", K.SECTION_NUMBER, "1.1", "R1")
    second = mention(text, "punkt 9.9", K.SECTION_NUMBER, "9.9", "R1")
    file = document(
        "fil",
        "Kravkatalog",
        T.REQUIREMENTS_CATALOGUE,
        (section(0, "1.1", "Allmänt", text),),
        (first, second),
    )

    references = resolve([file])

    assert [r.mention for r in references] == [first, second]
    assert [r.status for r in references] == [S.RESOLVED, S.NUMBER_MISSING]
