"""Ingestion step 4: references to the other files of the same agreement page.

What:
    `build_corpus` indexes all files and the agreement pages that link them.
    The rules here resolve a reference to a file listed on the same page as the
    file it is in (avropa.se lists each agreement's documents on one page):
    - R2 `resolve_document`: a named document ("enligt Allmänna villkor").
    - R1x `resolve_named_number`: a section number with a document named next to
      it ("p. 6.19.7 i Allmänna villkor").
    - R3 `resolve_annex_number`: "bilaga 3", by a link titled "Bilaga 3 ...".
    - R5 `resolve_annex_name`: "bilaga Priser", by a link title or an alias.
    - R4, second step, `resolve_title_on_page`: a section title that no heading
      of the file itself has, in the other files of the page.

Why:
    A reference names a document as the text calls it, and the page lists it
    under a link title: "Allmänna villkor" is the link "Allmänna villkor", "bilaga
    Priser" the link "Prisbilaga - sammanställning Delområde 1", "Huvuddokumentet"
    the link "Ramavtalets huvuddokument" (`document_names.ALIASES`). Only the
    page says which file that is, and a template linked from several pages can
    point to a different file on each.

How:
    Each rule looks on every page that links the file and collects the
    candidates per page. The outcome (`page_outcome`):
    - SELF when one of them is the file itself: the name is the file's own.
    - RESOLVED when every page with a candidate has exactly one. When the pages
      give different files, there is one target per page (with `page_url`),
      otherwise one target with `page_url` None.
    - AMBIGUOUS when a page has several, e.g. "Säkerhetsskyddsavtal" on a page
      with "Utkast till Säkerhetsskyddsavtal (Nivå 1)", "(Nivå 2)" and "(Nivå 3)".
      The candidates are the targets, in the order of `Corpus.pages`.
    - NOT_PUBLISHED when no page has one: a tender form that was never published,
      or a file step 1 does not fetch (.xlsx), which is not in the corpus.
    Link titles are compared as `document_names.normalise_title` gives them.
    Measured on the 207 pilot files (references counted in the rate, rules
    only): R2 2,053 of 2,933 resolved, R1x 183 of 225, R3 55 of 92, R5 259 of
    1,268 (998 not published), R4's second step 147.

    Not built (each covers fewer than 50 references in the pilot): a
    Säkerhetsskyddsavtal with its level named ("Säkerhetsskyddsavtal nivå 2",
    19), a number found by its suffix in the named document ("Allmänna villkor,
    punkt 31.1" for 6.31.1, 1).
"""

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass

from avtalsagent.domain.documents import CatalogLink
from avtalsagent.domain.extracted import (
    DocumentGroup,
    DocumentType,
    Reference,
    ReferenceMention,
    ReferenceStatus,
    ReferenceTarget,
)
from avtalsagent.ingestion.extract.document_names import ALIASES, normalise_title
from avtalsagent.ingestion.extract.resolve_in_document import (
    CorpusDocument,
    DocumentIndex,
    index_document,
    make_reference,
    own_targets,
)

# A candidate target: a file, and a section in it or None for the whole file.
Candidate = tuple[str, int | None]

# A supplier's signed "Ramavtal" (its card on the page) is its own main document:
# 7a49e1a61b31 §9.2.1: "3. Ramavtalets Huvuddokument med bilagor" is the Ramavtal itself,
# not the area's main document (75 references).
_SUPPLIER_CARD_NAMES = frozenset(("huvuddokument", "ramavtalets huvuddokument"))
# The plural "upphandlingsdokumenten" is the whole tender package: 80578a77ea47 §5.5:
# "3. Upphandlingsdokumenten med bilagor inklusive rättelser" (109 references).
_TENDER_PACKAGE = re.compile(r"(?i:upphandlingsdokumenten)s?")
# An annex name starts with a link title only where a word of the name ends, or a Swedish
# ending follows: "bilaga Avropsmallen" is the link "Avropsmall", "bilaga Kravkatalogs
# definitioner" the link "Kravkatalog", but "bilaga Ramavtalsleverantörens prislista" is not
# a supplier's card "Ramavtal".
_WORD_ENDING = re.compile(r"(?:s|n|en|et|ns|ens|ets)?(?!\w)")


@dataclass(frozen=True)
class Corpus:
    """All files, and for each agreement page the links to files of the corpus."""

    files: Mapping[str, DocumentIndex]  # by sha256
    # Page url -> its links to files of the corpus, in the order of the input files and
    # then of each file's links; not the order avropa.se shows them in.
    pages: Mapping[str, tuple[CatalogLink, ...]]

    def pages_of(self, index: DocumentIndex) -> list[str]:
        """The pages that link a file, in the order of its links."""
        return list(dict.fromkeys(link.page_url for link in index.document.links))

    def links_on(self, page: str) -> tuple[CatalogLink, ...]:
        return self.pages.get(page, ())

    def type_of(self, sha256: str) -> DocumentType:
        return self.files[sha256].document.metadata.document_type


def build_corpus(documents: Sequence[CorpusDocument]) -> Corpus:
    files = {document.sha256: index_document(document) for document in documents}
    pages: dict[str, list[CatalogLink]] = {}
    for document in documents:
        for link in document.links:
            pages.setdefault(link.page_url, []).append(link)
    return Corpus(files, {page: tuple(links) for page, links in pages.items()})


def by_page(
    corpus: Corpus, index: DocumentIndex, select: Callable[[str], Iterable[Candidate]]
) -> dict[str, list[Candidate]]:
    """The candidates `select` gives on each page of the file, without repeats."""
    return {page: list(dict.fromkeys(select(page))) for page in corpus.pages_of(index)}


def page_outcome(
    index: DocumentIndex,
    mention: ReferenceMention,
    found: Mapping[str, Sequence[Candidate]],
    rule: str,
) -> Reference | None:
    """SELF, RESOLVED or AMBIGUOUS from the candidates per page; None when there are none."""
    found = {page: candidates for page, candidates in found.items() if candidates}
    if not found:
        return None
    if any(sha256 == index.sha256 for candidates in found.values() for sha256, _ in candidates):
        return make_reference(index, mention, ReferenceStatus.SELF, rule)
    one_each = all(len(candidates) == 1 for candidates in found.values())
    status = ReferenceStatus.RESOLVED if one_each else ReferenceStatus.AMBIGUOUS
    return make_reference(index, mention, status, rule, targets_by_page(found))


def targets_by_page(found: Mapping[str, Sequence[Candidate]]) -> tuple[ReferenceTarget, ...]:
    """One target per page and candidate; page_url None when all pages give the same."""
    if len({tuple(candidates) for candidates in found.values()}) == 1:
        candidates = next(iter(found.values()))
        return tuple(ReferenceTarget(sha256=s, section=p, page_url=None) for s, p in candidates)
    return tuple(
        ReferenceTarget(sha256=sha256, section=section, page_url=page)
        for page, candidates in found.items()
        for sha256, section in candidates
    )


# --- Documents by name -----------------------------------------------------------------


def name_and_aliases(name: str) -> tuple[str, ...]:
    """The link titles a document name stands for: itself and its aliases (normalised)."""
    return (name, *ALIASES.get(name, ()))


def find_document(corpus: Corpus, index: DocumentIndex, name: str) -> dict[str, list[Candidate]]:
    """The files on each page whose link title starts with the name or one of its aliases."""
    prefixes = name_and_aliases(name)

    def select(page: str) -> Iterable[Candidate]:
        for link in corpus.links_on(page):
            if normalise_title(link.title).startswith(prefixes):
                yield link.sha256, None

    return by_page(corpus, index, select)


def procurement_files(
    corpus: Corpus, index: DocumentIndex, page: str, *, group: bool = False
) -> list[str]:
    """The procurement documents on a page other than the file itself.

    With `group`, the whole procurement group: the questions-and-answers logs too.
    """
    found = []
    for link in corpus.links_on(page):
        file = corpus.files[link.sha256].document.metadata
        if link.sha256 == index.sha256 or link.sha256 in found:
            continue
        if file.document_type is DocumentType.PROCUREMENT_DOCUMENT or (
            group and file.group is DocumentGroup.PROCUREMENT
        ):
            found.append(link.sha256)
    return found


def resolve_document(corpus: Corpus, index: DocumentIndex, mention: ReferenceMention) -> Reference:
    """R2: a named document, by the link titles of the page.

    In order:
    1. A supplier's card names itself "Huvuddokumentet": SELF.
    2. The name is a link title of the file itself (the Allmänna villkor naming
       "Allmänna villkor"): SELF.
    3. The file has a top-level chapter of that name, and is a procurement
       document or the page lists no file of that name: that chapter. A tender
       document holds the agreement documents as chapters, numbered differently
       from the published ones: 11db2f3d1852 §5.18: "I Allmänna villkor finns dock
       en rätt för Avropsberättigad" is its own chapter 6 "Allmänna Villkor". In
       other files a chapter of that name only describes the document the page
       publishes: the requirements reports' "2 Kravkatalog" (5ff547269162 §1:
       "Dessa villkor finns i ramavtalets Huvuddokument och i Allmänna villkor
       samt i Kravkatalog") is the page's Kravkatalog.
    4. "Upphandlingsdokumenten", the tender package: every procurement document
       and questions log of the page, AMBIGUOUS when there are several.
    5. The files of each page whose link title starts with the name or an alias
       (`page_outcome`); NOT_PUBLISHED when there are none.
    """
    metadata = index.document.metadata
    if metadata.document_type is DocumentType.SUPPLIER_AGREEMENT and (
        mention.key in _SUPPLIER_CARD_NAMES
    ):
        return make_reference(index, mention, ReferenceStatus.SELF, "R2")
    found = find_document(corpus, index, mention.key)
    if any(sha256 == index.sha256 for candidates in found.values() for sha256, _ in candidates):
        return make_reference(index, mention, ReferenceStatus.SELF, "R2")
    chapter = index.chapter(name_and_aliases(mention.key))
    if chapter is not None and (
        metadata.document_type is DocumentType.PROCUREMENT_DOCUMENT or not any(found.values())
    ):
        return make_reference(
            index, mention, ReferenceStatus.RESOLVED, "R2", own_targets(index, chapter.position)
        )
    if names_tender_package(mention):
        package = by_page(
            corpus,
            index,
            lambda page: ((s, None) for s in procurement_files(corpus, index, page, group=True)),
        )
        found = package if any(package.values()) else found
    outcome = page_outcome(index, mention, found, "R2")
    return outcome or make_reference(index, mention, ReferenceStatus.NOT_PUBLISHED, "R2")


def names_tender_package(mention: ReferenceMention) -> bool:
    """Whether a mention is "upphandlingsdokumenten", the whole tender package."""
    return _TENDER_PACKAGE.fullmatch(mention.raw) is not None


def resolve_named_number(
    corpus: Corpus, index: DocumentIndex, mention: ReferenceMention, name: str
) -> Reference:
    """R1x: a section number with a document named next to it (`name`).

    The document is found as by R2; then the number in it. In order:
    1. The document is the file itself: the number in the file.
    2. The file has the document as a top-level chapter and the number lies in that
       chapter: that section.
    3. The named files of each page that have the number (`page_outcome`).
    4. The procurement documents of the page that have the document as a
       top-level chapter with the number in it. bdf58b81d100 §42: "p. 6.19.7 i
       Allmänna villkor" is in chapter 6 "Allmänna villkor" of the tender
       documents, not in the published Allmänna villkor (numbered 2.x); the
       question's date then picks the Ansökningsinbjudan.
    5. NUMBER_MISSING with the named files as targets, or NOT_PUBLISHED when the
       page has no such file.
    """
    found = find_document(corpus, index, name)
    if any(s == index.sha256 for candidates in found.values() for s, _ in candidates):
        position = index.numbered(mention.key)
        if position is None:
            return make_reference(
                index, mention, ReferenceStatus.NUMBER_MISSING, "R1x", own_targets(index, None)
            )
        return make_reference(
            index, mention, ReferenceStatus.RESOLVED, "R1x", own_targets(index, position)
        )
    position = _in_chapter(index, name, mention.key)
    if position is not None:
        return make_reference(
            index, mention, ReferenceStatus.RESOLVED, "R1x", own_targets(index, position)
        )
    with_number = {
        page: [
            (sha256, position)
            for sha256, _ in candidates
            if (position := corpus.files[sha256].numbered(mention.key)) is not None
        ]
        for page, candidates in found.items()
    }
    in_chapters = by_page(
        corpus,
        index,
        lambda page: (
            (sha256, position)
            for sha256 in procurement_files(corpus, index, page)
            if (position := _in_chapter(corpus.files[sha256], name, mention.key)) is not None
        ),
    )
    outcome = page_outcome(index, mention, with_number, "R1x") or page_outcome(
        index, mention, in_chapters, "R1x"
    )
    if outcome:
        return outcome
    if any(found.values()):
        targets = targets_by_page({page: c for page, c in found.items() if c})
        return make_reference(index, mention, ReferenceStatus.NUMBER_MISSING, "R1x", targets)
    return make_reference(index, mention, ReferenceStatus.NOT_PUBLISHED, "R1x")


def _in_chapter(index: DocumentIndex, name: str, number: str) -> int | None:
    """The section `number` of a file, if it lies in a top-level chapter called `name`."""
    chapter = index.chapter(name_and_aliases(name))
    position = index.numbered(number)
    if chapter is None or position is None:
        return None
    if chapter.number and (number == chapter.number or number.startswith(f"{chapter.number}.")):
        return position
    return None


# --- Annexes ---------------------------------------------------------------------------


def resolve_annex_number(
    corpus: Corpus, index: DocumentIndex, mention: ReferenceMention
) -> Reference:
    """R3: "bilaga N" resolves to the link titled "Bilaga N ..." on the page.

    171a3cacf5fd §10.1: "Bilaga 5.1 Enterprise-avtal" resolves to the link
    "Bilaga 5.1 Enterprise-avtal". Only the Microsoft page numbers its links; its
    guidance (c27b833f338d §2.4.1: 'återfinns i sin helhet Bilaga 5 "Tillägg och
    förtydliganden ..."') cites them as the agreement does. Not from licence
    terms: Microsoft's and IBM's own terms number their own annexes, which are no
    files of the page (e0db1184576c §pos8: 'Med "GDPR-villkor" avses villkoren i
    Bilaga 1'); NOT_PUBLISHED.
    """
    if index.document.metadata.document_type is DocumentType.LICENCE_TERMS:
        return make_reference(index, mention, ReferenceStatus.NOT_PUBLISHED, "R3")
    title = re.compile(rf"bilaga {re.escape(mention.key)}(?![\d.])")

    def select(page: str) -> Iterable[Candidate]:
        for link in corpus.links_on(page):
            if title.match(normalise_title(link.title)):
                yield link.sha256, None

    outcome = page_outcome(index, mention, by_page(corpus, index, select), "R3")
    return outcome or make_reference(index, mention, ReferenceStatus.NOT_PUBLISHED, "R3")


def resolve_annex_name(
    corpus: Corpus, index: DocumentIndex, mention: ReferenceMention
) -> Reference:
    """R5: "bilaga <Namn>" resolves by the link titles of the page.

    1. The longest link title on each page that the name starts with
       (11db2f3d1852 §2.6: "bilaga Utkast till Datadelningsavtal" is the link
       "Utkast till datadelningsavtal"). Both are compared without "Utkast till"
       and with hyphens between letters removed: 0486216326ec §pos0, the cover
       "Bilaga Utkast till personuppgiftsbiträdes-avtal", is the file's own link.
    2. Else the document the name starts with (`ReferenceMention.document_name`:
       34d71a7e4da0 §8.4 "Bilaga Avropsrutin" is the link "Avropsrutin och
       Kravkatalog"), or an alias such as "Priser" (34d71a7e4da0 §8.10.1: "anges
       i bilaga Priser" is the link "Prisbilaga" on each of its two pages), found
       as by R2.
    3. Else NOT_PUBLISHED: most annex names are tender forms that the page does
       not publish ("bilaga Kvalitet i utförande av Konsulttjänst", "bilaga
       Avropsberättigade").
    """
    key = normalise_title(mention.key, join_hyphenated=True)

    def select(page: str) -> Iterable[Candidate]:
        best: list[Candidate] = []
        best_length = 0
        for link in corpus.links_on(page):
            title = normalise_title(link.title, join_hyphenated=True)
            if len(title) < best_length or not _starts_with_word(key, title):
                continue
            if len(title) > best_length:
                best, best_length = [], len(title)
            best.append((link.sha256, None))
        return best

    outcome = page_outcome(index, mention, by_page(corpus, index, select), "R5")
    if outcome:
        return outcome
    name = mention.document_name or next(
        (alias for alias in ALIASES if _starts_with_word(key, alias)), None
    )
    if name:
        outcome = page_outcome(index, mention, find_document(corpus, index, name), "R5")
        if outcome:
            return outcome
    return make_reference(index, mention, ReferenceStatus.NOT_PUBLISHED, "R5")


def _starts_with_word(name: str, start: str) -> bool:
    """Whether `name` starts with `start` followed by the end of a word or an ending."""
    return name.startswith(start) and _WORD_ENDING.match(name, len(start)) is not None


# --- Section titles in other files ------------------------------------------------------


def resolve_title_on_page(
    corpus: Corpus, index: DocumentIndex, mention: ReferenceMention
) -> Reference | None:
    """R4, second step: a title no heading of the file has, in the other files of the page.

    1d58dc2e8387 §3.1.1: "Se Kravkatalog avsnitt Hållbarhet." resolves to 8.1.3
    "Hållbarhet" of the page's Kravkatalog. On each page, the files with the
    longest heading the text starts with count. The procurement documents count
    only when no other file has it: they hold the agreement documents as chapters
    and so repeat their headings (a61f1d5b1580 §2: "Avsnitt
    Personuppgiftsbehandling" is 7.7 of the Kravkatalog, not 7.7 of the
    Anbudsinbjudan or the Ansökningsinbjudan; 54 references). RESOLVED when one
    section of each page is left, AMBIGUOUS when several are (0692da436391
    §1.15.2.1: "Avsnitt Underleverantörer" is a heading of the three
    Säkerhetsskyddsavtal and of the Vägledning); None when no file has it.
    """

    def select(page: str) -> list[Candidate]:
        matches = []
        for link in corpus.links_on(page):
            file = corpus.files[link.sha256]
            if match := file.heading_match(mention.key):
                matches.append((len(match[0]), file, match[1]))
        longest = max((length for length, _, _ in matches), default=0)
        found = [(file, positions) for length, file, positions in matches if length == longest]
        published = [
            (file, positions)
            for file, positions in found
            if file.document.metadata.group is not DocumentGroup.PROCUREMENT
        ]
        return [(file.sha256, p) for file, positions in published or found for p in positions]

    return page_outcome(index, mention, by_page(corpus, index, select), "R4")
