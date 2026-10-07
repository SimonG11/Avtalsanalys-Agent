"""Ingestion step 4: the files as the resolver reads them, and references within one file.

What:
    `CorpusDocument` is one file with what step 4 found in it: its metadata,
    sections, reference mentions and the links that point to it. `index_document`
    makes a `DocumentIndex` of it, which finds a section by its number or by the
    start of its heading. With it, `resolve_number` (R1) resolves "punkt 6.21.9"
    to the section numbered 6.21.9 in the same file, and `resolve_title` (R4, first
    step) resolves "enligt avsnitt Avtalsbrott och påföljder" to the section with
    that heading in the same file. `make_reference` builds the `Reference` all
    resolver rules return.

Why:
    Most references point into the file they are in: in the pilot, 306 of the 339
    section numbers outside the questions logs with no document named (R1), and
    3,219 of the 3,788 section titles R4 looks up, resolve in the same file. A
    title reference names the heading and goes on with the sentence ("enligt
    avsnitt Avtalsbrott och påföljder."), and Kammarkollegiet's documents from
    2023 on cite sections by title more often than by number, so a title must be
    found by its start.

How:
    Section numbers are compared as they are. Headings are compared in lower case
    and without a final dot or colon (66b60a8f74a0 §5.8 cites "avsnitt
    Uteslutningsgrunder ... m.m" at the end of a sentence, the heading ends
    "m.m."). A title reference resolves to the longest heading its text starts
    with, when a character that is not a letter or digit (or the end) follows. A
    heading the file has more than once is taken from the reference's own chapter
    (`nearest_in_outline`, 340 references); if that does not decide, the result is
    AMBIGUOUS with each section.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from avtalsagent.domain.documents import CatalogLink
from avtalsagent.domain.extracted import (
    DocumentMetadata,
    Reference,
    ReferenceMention,
    ReferenceStatus,
    ReferenceTarget,
)
from avtalsagent.domain.parsed import Section

# "bilagans avsnitt X" is a section of a tender form named before it, which the page
# does not publish: 66b60a8f74a0 §4.5.2.2: '0 poäng för bilagans avsnitt "Kvalitet i
# utförandet hos kund - kunds bedömning"'.
_OF_THE_ANNEX = re.compile(r"(?i:bilagans)\s+$")
# A tender document holds the agreement documents as chapters, in the first phase as
# drafts: f1bebd6b7ddb chapter 5 "Utkast Ramavtalets Huvuddokument".
_DRAFT = re.compile(r"^utkast\s+(?:till\s+)?")


@dataclass(frozen=True)
class CorpusDocument:
    """One file as the resolver sees it: what step 4 found in it and where it is linked."""

    metadata: DocumentMetadata
    sections: tuple[Section, ...]
    mentions: tuple[ReferenceMention, ...]
    # The links to this file, one per agreement page (and title) it is listed on.
    links: tuple[CatalogLink, ...]

    @property
    def sha256(self) -> str:
        return self.metadata.sha256


@dataclass(frozen=True)
class DocumentIndex:
    """A file's sections, by position, by number and by heading."""

    document: CorpusDocument
    sections: Mapping[int, Section]  # by position
    numbers: Mapping[str, int]  # normalised section number -> position
    headings: Mapping[str, tuple[int, ...]]  # normalised heading -> positions

    @property
    def sha256(self) -> str:
        return self.document.sha256

    def numbered(self, number: str) -> int | None:
        """The position of the section with this number, if the file has one."""
        return self.numbers.get(number)

    def heading_match(self, text: str) -> tuple[str, tuple[int, ...]] | None:
        """The longest heading `text` starts with, and the sections that have it.

        The heading must be followed by a character that is not a letter or digit,
        or end the text: "Avtalsbrott och påföljder." starts with the heading
        "Avtalsbrott och påföljder", "Priser" does not start with "Pris". Short
        headings count: 198d61824b3b §6.10 "utgör avvikelserna Fel enligt avsnitt
        Fel." cites the section "Fel".
        """
        key = normalise_heading(text)
        for end in range(len(key), 0, -1):
            if end < len(key) and key[end].isalnum():
                continue
            if key[:end] in self.headings:
                return key[:end], self.headings[key[:end]]
        return None

    def chapter(self, titles: Sequence[str]) -> Section | None:
        """The first top-level section whose heading, without "Utkast", is one of `titles`."""
        for section in self.document.sections:
            heading = _DRAFT.sub("", normalise_heading(section.title))
            if section.level == 1 and heading in titles:
                return section
        return None


def index_document(document: CorpusDocument) -> DocumentIndex:
    numbers: dict[str, int] = {}
    headings: dict[str, tuple[int, ...]] = {}
    for section in document.sections:
        if section.number:
            numbers.setdefault(section.number, section.position)
        if heading := normalise_heading(section.title):
            headings[heading] = (*headings.get(heading, ()), section.position)
    sections = {section.position: section for section in document.sections}
    return DocumentIndex(document, sections, numbers, headings)


def normalise_heading(text: str) -> str:
    """Lower case, without a final dot or colon: "Uteslutningsgrunder ... m.m." -> "... m.m"."""
    return text.lower().rstrip(" .:")


def make_reference(
    index: DocumentIndex,
    mention: ReferenceMention,
    status: ReferenceStatus,
    rule: str | None,
    targets: Sequence[ReferenceTarget] = (),
) -> Reference:
    return Reference(
        sha256=index.sha256, mention=mention, status=status, rule=rule, targets=tuple(targets)
    )


def own_targets(index: DocumentIndex, *sections: int | None) -> tuple[ReferenceTarget, ...]:
    """Targets in the file itself: the given sections, or the whole file (None)."""
    return tuple(
        ReferenceTarget(sha256=index.sha256, section=section, page_url=None) for section in sections
    )


def resolve_number(index: DocumentIndex, mention: ReferenceMention) -> Reference:
    """R1: a section number with no document named resolves in the same file.

    21d3f9cdf880 §1.1.2.6: "(se punkt 1.1.2.4)" resolves to 1.1.2.4 of the same
    file. Otherwise NUMBER_MISSING, with the file itself as the target that was
    searched (54211e718d8e §9.1: "(se avsnitt 5.2 och 5.3)", an older numbering).
    """
    position = index.numbered(mention.key)
    if position is None:
        return make_reference(
            index, mention, ReferenceStatus.NUMBER_MISSING, "R1", own_targets(index, None)
        )
    return make_reference(
        index, mention, ReferenceStatus.RESOLVED, "R1", own_targets(index, position)
    )


def resolve_title(index: DocumentIndex, mention: ReferenceMention) -> Reference | None:
    """R4, first step: a section title resolves to a heading of the same file.

    4f5a5c8becf6 §1.12.3: "enligt avsnitt Avtalsbrott och påföljder." resolves to
    the section "Avtalsbrott och påföljder" of the same main document. A heading
    the file has more than once is taken from where the reference is
    (`nearest_in_outline`). None when no heading of the file starts the text.
    """
    match = index.heading_match(mention.key)
    if match is None:
        return None
    found = nearest_in_outline(index, mention.section, match[1])
    status = ReferenceStatus.RESOLVED if len(found) == 1 else ReferenceStatus.AMBIGUOUS
    return make_reference(index, mention, status, "R4", own_targets(index, *found))


def nearest_in_outline(
    index: DocumentIndex, position: int, candidates: Sequence[int]
) -> tuple[int, ...]:
    """Of the sections that share a heading, those closest to section `position`.

    A supplier's Ramavtal ends each obligation with a section "Vite vid
    avtalsbrott" (9.7.8, 9.11.3, 9.15.5, ...); 9.17.1 "enligt avsnitt Vite vid
    avtalsbrott" means 9.17.4, the one in its own chapter (e1361d5fd2b0). Closest
    is the most leading number parts in common, at least the chapter; of those,
    the highest level (9.11 before 9.11.2, both "Information om Ramavtalet").
    Unnumbered sections, or none in the same chapter, leave all candidates.
    """
    own = _number_parts(index.sections[position])
    shared = {
        candidate: _shared(own, _number_parts(index.sections[candidate]))
        for candidate in candidates
    }
    most = max(shared.values(), default=0)
    if most == 0:
        return tuple(candidates)
    closest = [candidate for candidate in candidates if shared[candidate] == most]
    top = min(index.sections[candidate].level for candidate in closest)
    return tuple(candidate for candidate in closest if index.sections[candidate].level == top)


def _number_parts(section: Section) -> list[str]:
    return section.number.split(".") if section.number else []


def _shared(first: Sequence[str], second: Sequence[str]) -> int:
    """How many leading parts two section numbers have in common."""
    count = 0
    for one, other in zip(first, second, strict=False):
        if one != other:
            break
        count += 1
    return count


def title_not_found(
    index: DocumentIndex, mention: ReferenceMention, searched: Sequence[ReferenceTarget]
) -> Reference:
    """R4 when no heading was found: NOT_PUBLISHED after "bilagans", else TITLE_MISSING.

    TITLE_MISSING carries the file that was searched (`searched`), so the language
    model step can choose among its headings (564af12ada04 §9.18.2: "enligt avsnitt
    Försäljningsredovisning och administrativ avgift", whose heading says
    "administrationsavgift").
    """
    text = index.sections[mention.section].text
    if _OF_THE_ANNEX.search(text[max(0, mention.start - 20) : mention.start]):
        return make_reference(index, mention, ReferenceStatus.NOT_PUBLISHED, "R4")
    return make_reference(index, mention, ReferenceStatus.TITLE_MISSING, "R4", searched)
