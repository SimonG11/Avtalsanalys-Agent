"""Tests for avtalsagent.ingestion.extract.resolve_amendments: covered files, R1a and R4a.

The titles, headings and sentences are from the Microsoft and IBM pages on avropa.se
(2026-10-05), named by sha256 prefix and section next to each test. Made up for the
tests: the file ids ("bilaga-4.1"), and the lines marked "made up".
"""

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
from avtalsagent.ingestion.extract.reference_resolver import CorpusDocument, resolve
from avtalsagent.ingestion.extract.resolve_amendments import covered_files
from avtalsagent.ingestion.extract.resolve_on_page import build_corpus

PROGRAMS = "https://www.avropa.se/ramavtal/ramavtalsomraden/programvaror-och-tjanster/"
MICROSOFT = PROGRAMS + "Programvaror-och-tjanster/volymavtal-for-microsoft/"
IBM = PROGRAMS + "Programvaror-och-tjanster/volymavtal-for-ibm/"
T = DocumentType
K = ReferenceKind
S = ReferenceStatus


def section(position: int, number: str | None, title: str, text: str = "") -> Section:
    return Section(
        position=position,
        number=number,
        title=title,
        level=1,
        parent=None,
        path=(),
        page_start=None,
        page_end=None,
        text=text or f"{number or ''} {title}".strip(),
    )


def document(
    sha256: str,
    title: str,
    document_type: DocumentType,
    sections: tuple[Section, ...],
    mentions: tuple[ReferenceMention, ...] = (),
    *,
    annex_number: str | None = None,
    page: str = MICROSOFT,
) -> CorpusDocument:
    metadata = DocumentMetadata(
        sha256=sha256,
        title=title,
        document_type=document_type,
        type_rule="test",
        agreement_number=None,
        annex_number=annex_number,
        first_chapter=None,
        tendsign_cover=None,
        is_template=False,
        version_date=None,
        version_rule=None,
        published_on=None,
        site_updated=None,
    )
    link = CatalogLink(
        sha256=sha256,
        url=f"https://www.avropa.se/globalassets/{sha256}.pdf",
        title=title,
        category=None,
        agreement_number=None,
        site_updated=None,
        page_url=page,
        page_title="Programvaror och tjänster",
        page_procurement_numbers=("23.3-9725-2023",),
        page_period=None,
    )
    return CorpusDocument(metadata, sections, mentions, (link,))


def annex(
    number: str, title: str, *sections: Section, page: str = MICROSOFT, sha256: str = ""
) -> CorpusDocument:
    """A file of the page titled "Bilaga <number> <title>", by default with id "bilaga-<number>"."""
    return document(
        sha256 or f"bilaga-{number}",
        f"Bilaga {number} {title}",
        T.LICENCE_TERMS,
        sections or (section(0, None, title),),
        annex_number=number,
        page=page,
    )


def amendment(
    title: str,
    text: str = "",
    raw: str = "",
    kind: ReferenceKind = K.SECTION_NUMBER,
    key: str = "",
    *sections: Section,
) -> CorpusDocument:
    """An amendment titled `title` ("Bilaga 4 Tillägg ...") with `sections` after section 0.

    Section 0 has `text`, and in it the one mention `raw` when one is given.
    """
    mentions: tuple[ReferenceMention, ...] = ()
    if raw:
        start = text.index(raw)
        mentions = (
            ReferenceMention(
                section=0, start=start, end=start + len(raw), raw=raw, kind=kind, key=key, rule="t"
            ),
        )
    number = title.split()[1]
    return document(
        f"bilaga-{number}",
        title,
        T.AMENDMENT,
        (section(0, None, "Text före första rubriken", text), *sections),
        mentions,
        annex_number=number,
    )


def outcome(*documents: CorpusDocument) -> tuple[ReferenceStatus, str | None, list[str]]:
    """Status, rule and targets ("bilaga-4.1 §10") of the first mention of the first file."""
    reference: Reference = resolve(documents)[0]
    files = {document.sha256: document for document in documents}
    return reference.status, reference.rule, [_named(t, files) for t in reference.targets]


def _named(target: ReferenceTarget, files: dict[str, CorpusDocument]) -> str:
    if target.section is None:
        return target.sha256
    [found] = [s for s in files[target.sha256].sections if s.position == target.section]
    return f"{target.sha256} §{found.number or found.title}"


# The Microsoft page on 2026-10-05: the annexes the amendments amend, with some of their
# sections (005469cd3990, 7c093254b164, aba1280d90be, 36d5c9a448ce, bea056be2d79,
# 70cb314c49b6, d182220a8038, 38518d009693).
MBSA = annex("4.1", "Microsoft Business and Services Agreement", section(10, "10", "Övrigt."))
ENTERPRISE = annex(
    "5.1",
    "Enterprise-avtal",
    section(2, "2", "Licenser för Produkter."),
    section(3, "3", "Framställning av exemplar av Produkter och re-imaging."),
    section(6, "6", "Övrigt."),
)
REGISTRATION = annex(
    "5.2",
    "Enterprise-registrering",
    section(1, "2", "Beställningskrav."),
    section(2, "3", "Prissättning."),
)
SUBSCRIPTION = annex(
    "5.3",
    "Enterprise Subscription-registrering",
    section(1, "2", "Beställningskrav."),
    section(2, "3", "Prissättning."),
)
SERVER = annex(
    "5.4",
    "Server and Cloud-registrering",
    section(1, "2", "Användningsrättigheter."),
    section(2, "3", "Beställningskrav."),
    section(3, "4", "Prissättning."),
)
SELECT_PLUS = annex("6.1", "Select Plus-avtal", section(10, "10", "Övrigt."))
CAMPUS = annex("7.1", "Campus- och schoolavtal", section(13, None, "Övrigt."))
EDUCATION = annex("8", "Enrollment for Education Solutions", section(3, "3", "Prissättning."))
REGISTRATIONS = (ENTERPRISE, REGISTRATION, SUBSCRIPTION, SERVER)
# The IBM page numbers files 5.2 and 5.3 too (b6a94d1c1a90, 96f94a0bb585).
IBM_FILES = (
    annex("5.2", "PA Government Attachment", page=IBM, sha256="ibm-5.2"),
    annex("5.3", "IPLA", page=IBM, sha256="ibm-5.3"),
)
# The amendments' titles on the page.
AMENDS_4 = "Bilaga 4 Tillägg och förtydliganden till bilaga 4.1"  # d54ed0900be5
AMENDS_5 = "Bilaga 5 Tillägg och förtydligande till bilagorna 5.1-5.4"  # 5aab54c5a4b1
AMENDS_7 = "Bilaga 7 Tillägg och förtydliganden till bilaga 7.1"  # 386122e82b7b


# --- The files an amendment amends --------------------------------------------------------


def covered(amending: CorpusDocument, *others: CorpusDocument) -> list[str]:
    corpus = build_corpus((amending, *others))
    by_page = covered_files(corpus, corpus.files[amending.sha256])
    return [sha256 for files in by_page.values() for sha256 in files]


def test_an_amendment_covers_the_annexes_its_title_names() -> None:
    page = (MBSA, *REGISTRATIONS, SELECT_PLUS, EDUCATION)
    assert covered(amendment(AMENDS_5), *page) == [
        "bilaga-5.1",
        "bilaga-5.2",
        "bilaga-5.3",
        "bilaga-5.4",
    ]
    assert covered(amendment(AMENDS_4), *page) == ["bilaga-4.1"]


def test_only_the_files_on_the_amendments_own_page_are_covered() -> None:
    assert covered(amendment(AMENDS_5), REGISTRATION, *IBM_FILES) == ["bilaga-5.2"]


def test_a_file_that_is_no_amendment_covers_nothing() -> None:
    # Made up: the same title on a file of another type.
    other = annex("5", "Tillägg och förtydligande till bilagorna 5.1-5.4")
    assert covered(other, *REGISTRATIONS) == []


# --- R1a: a section number in the files amended -------------------------------------------

# 5aab54c5a4b1 §pos0.
ITEM_2A = "A. Punkt 2a. i Registreringen ersätts med följande: Minsta beställningskrav."


def test_r1a_looks_up_a_number_without_its_letter_in_each_file_amended() -> None:
    amending = amendment(AMENDS_5, ITEM_2A, "Punkt 2a", K.SECTION_NUMBER, "2a")
    assert outcome(amending, *REGISTRATIONS) == (
        S.AMBIGUOUS,
        "R1a",
        ["bilaga-5.1 §2", "bilaga-5.2 §2", "bilaga-5.3 §2", "bilaga-5.4 §2"],
    )
    assert outcome(amending, REGISTRATION) == (S.RESOLVED, "R1a", ["bilaga-5.2 §2"])


def test_r1a_comes_before_a_number_of_the_amendment_itself() -> None:
    # 5aab54c5a4b1 §pos0, whose own list has an item 3 ("3. Med undantag för ...").
    text = (
        'B. Punkten 3.b. "Exemplar för utbildning/utvärdering och säkerhetskopiering" i Avtalet '
        "ändras härmed och ersätts i sin helhet med följande:"
    )
    own_item = section(3, "3", "Med undantag för kostnadsfria provperioder och LinkedIn-tjänster")
    amending = amendment(AMENDS_5, text, "Punkten 3", K.SECTION_NUMBER, "3", own_item)
    assert outcome(amending, ENTERPRISE) == (S.RESOLVED, "R1a", ["bilaga-5.1 §3"])
    # Without the files it amends on the page, R1 finds the item.
    assert outcome(amending) == (S.RESOLVED, "R1", ["bilaga-5 §3"])


def test_r1a_a_number_no_file_amended_has_is_looked_up_in_the_amendment() -> None:
    # 386122e82b7b §pos1: the Campus agreement 7.1 does not number its sections.
    text = "A. Ikraftträdandedatum. Punkt 8.a skall i sin helhet strykas och ersättas med"
    amending = amendment(AMENDS_7, text, "Punkt 8", K.SECTION_NUMBER, "8")
    assert outcome(amending, CAMPUS) == (S.NUMBER_MISSING, "R1", ["bilaga-7"])


# --- R4a: a section title in the files amended ---------------------------------------------

# d54ed0900be5 §pos6 and 386122e82b7b §pos1.
OTHER_TERMS = (
    'Punkten "Övrigt" under underrubriken "Tvistlösning" i Microsoft Business and Services '
    "Avtal ändras härmed enligt följande:"
)


def test_r4a_a_title_the_amendment_lacks_resolves_in_the_file_it_amends() -> None:
    four = amendment(AMENDS_4, OTHER_TERMS, 'Punkten "Övrigt', K.SECTION_TITLE, "Övrigt")
    page = (MBSA, ENTERPRISE, SELECT_PLUS, CAMPUS)
    assert outcome(four, *page) == (S.RESOLVED, "R4a", ["bilaga-4.1 §10"])
    seven = amendment(AMENDS_7, OTHER_TERMS, 'Punkten "Övrigt', K.SECTION_TITLE, "Övrigt")
    assert outcome(seven, *page) == (S.RESOLVED, "R4a", ["bilaga-7.1 §Övrigt."])
    # Made up: a title that names no annex leaves the four files of the page with "Övrigt.".
    plain = amendment("Bilaga 4 Tillägg", OTHER_TERMS, 'Punkten "Övrigt', K.SECTION_TITLE, "Övrigt")
    assert outcome(plain, *page) == (
        S.AMBIGUOUS,
        "R4",
        ["bilaga-4.1 §10", "bilaga-5.1 §6", "bilaga-6.1 §10", "bilaga-7.1 §Övrigt."],
    )


def test_r4a_leaves_out_the_files_not_amended() -> None:
    # 5aab54c5a4b1 §4: Bilaga 8 has a section "Prissättning." too.
    text = 'F. Punkten "Prissättning" i Registrering 3b. ersätts i sin helhet med följande:'
    amending = amendment(AMENDS_5, text, 'Punkten "Prissättning', K.SECTION_TITLE, "Prissättning")
    assert outcome(amending, *REGISTRATIONS, EDUCATION) == (
        S.AMBIGUOUS,
        "R4a",
        ["bilaga-5.2 §3", "bilaga-5.3 §3", "bilaga-5.4 §4"],
    )


def test_r4a_comes_after_a_heading_of_the_amendment_itself() -> None:
    # 386122e82b7b §pos2 "Kommunikation."; made up: the mention and the same heading in 7.1.
    text = "Avseende punkten Kommunikation gäller följande."
    own = section(2, None, "Kommunikation.")
    amending = amendment(
        AMENDS_7, text, "punkten Kommunikation", K.SECTION_TITLE, "Kommunikation", own
    )
    campus = annex("7.1", "Campus- och schoolavtal", section(5, None, "Kommunikation."))
    assert outcome(amending, campus) == (S.RESOLVED, "R4", ["bilaga-7 §Kommunikation."])


def test_r4a_a_title_no_file_amended_has_goes_on_to_the_page() -> None:
    # d54ed0900be5 §pos6: no annex has a heading "Tvistlösning"; made up: Bilaga 8 has it.
    raw = 'underrubriken "Tvistlösning'
    amending = amendment(AMENDS_4, OTHER_TERMS, raw, K.SECTION_TITLE, "Tvistlösning")
    assert outcome(amending, MBSA) == (S.TITLE_MISSING, "R4", ["bilaga-4"])
    education = annex("8", "Enrollment for Education Solutions", section(9, "9", "Tvistlösning."))
    assert outcome(amending, MBSA, education) == (S.RESOLVED, "R4", ["bilaga-8 §9"])
