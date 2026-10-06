"""Ingestion step 4: references in a questions-and-answers log.

What:
    - RQ `resolve_question`: "fråga 17" resolves to question 17 of the same log.
    - R1q `resolve_question_number`: a section number in a question ("Angående
      5.13.1") resolves to the procurement document of the page that has it.
    - R4q `resolve_question_title`: a section title in a question, likewise.
    - `settle_by_date`: an AMBIGUOUS choice among procurement documents, made by
      the date of the question.
    - `question_targets`: the file that was searched, for a number or title
      that is not found.

Why:
    A log's questions are about the tender documents. Their numbers and headings
    ("Fråga kring p. 6.20.11 i Upphandlingsdokument") point into the
    Ansökningsinbjudan or the Anbudsinbjudan, not into the log, which numbers its
    questions. A page often has both documents (the two phases of a procurement),
    numbered alike. In the pilot R1q resolves 1,120 of the 1,325 section numbers
    in logs with no document named, R4q 64 of 82 titles and RQ 298 of 303
    questions. A whole number after "punkt" in a log is an item of a list
    (LIST_ITEM, set by `reference_patterns`), so it is not looked up.

How:
    R1q and R4q look on each page that links the log, in order:
    (a) the file the log's own link title names: "Frågor och svar -
        Ansökningsinbjudan" is about the link "Ansökningsinbjudan" of the page;
    (b) the procurement documents of the page that have the number or heading:
        RESOLVED when only one does, AMBIGUOUS when several do.
    `reference_resolver.resolve` passes every AMBIGUOUS reference of a log whose
    candidates are all procurement documents to `settle_by_date` (d): a question
    cannot be about a document published after it was asked, so the candidates
    published on or before the question date stay and the latest of them wins
    (521 references, from R1q, R1x, R2 and R4q; the tender package
    "upphandlingsdokumenten" only when one document was out). The question date
    is the earliest TendSign time stamp ("2024-05-30 16:14") in the question's
    section: the reading order mixes the question's and the answer's
    (bdf58b81d100 §122 shows the answer 2024-06-04 10:08 before the question
    2024-05-30 16:14). A document whose `published_on` is None (it says it is a
    later version: b2bf8baefe48 "Version 3: publicerad 2024-11-19") is never left
    out by date, and then nothing is chosen, since its first version may be older
    than the question. This leaves 169 number references of its log 3a316e27aadf
    AMBIGUOUS between it and the Ansökningsinbjudan f1bebd6b7ddb.
    Not built: (c) a number whose heading in the document matches the words after
    it in the question (46 references in the pilot; the date rule agrees with it
    in 48 of 51 cases where both apply).
"""

import re
from collections.abc import Callable, Iterable, Sequence
from datetime import date

from avtalsagent.domain.extracted import (
    DocumentType,
    Reference,
    ReferenceMention,
    ReferenceStatus,
    ReferenceTarget,
)
from avtalsagent.ingestion.extract.document_names import normalise_title
from avtalsagent.ingestion.extract.resolve_in_document import (
    DocumentIndex,
    make_reference,
    own_targets,
)
from avtalsagent.ingestion.extract.resolve_on_page import (
    Candidate,
    Corpus,
    by_page,
    names_tender_package,
    page_outcome,
    procurement_files,
    targets_by_page,
)

# The link title of a log names the document it is about: "Frågor och svar -
# Ansökningsinbjudan" (5de33501e165), "Frågor och svar - Upphandlingsdokument".
_LOG_TITLE = re.compile(r"^frågor och svar\s*-\s*(?P<document>.+)$")
# TendSign stamps each question and answer: "Datum: ... 2024-05-30 16:14".
_TIMESTAMP = re.compile(r"(\d{4}-\d{2}-\d{2}) \d{2}:\d{2}")


def is_questions_log(index: DocumentIndex) -> bool:
    return index.document.metadata.document_type is DocumentType.QUESTIONS_AND_ANSWERS


def resolve_question(index: DocumentIndex, mention: ReferenceMention) -> Reference:
    """RQ: "fråga N" resolves to question N of the same log.

    39d8c1efe373 §7: "Följdfråga på fråga 1 avseende 5.6.3.1
    Kvalitetsledningssystem" resolves to "Publik fråga 1".
    """
    position = index.numbered(mention.key)
    if position is None:
        return make_reference(
            index, mention, ReferenceStatus.NUMBER_MISSING, "RQ", own_targets(index, None)
        )
    return make_reference(
        index, mention, ReferenceStatus.RESOLVED, "RQ", own_targets(index, position)
    )


def resolve_question_number(
    corpus: Corpus, index: DocumentIndex, mention: ReferenceMention
) -> Reference:
    """R1q: a section number in a log, in the procurement documents of the page.

    39d8c1efe373 §82: "en väg vara att i avsnitt 7.8.1 Utbyte av konsult, tredje
    stycket, lägga till" resolves to 7.8.1 of the page's Anbudsinbjudan, the only
    procurement document there. NUMBER_MISSING when none has the number.
    """
    found = by_page(
        corpus, index, _candidates(corpus, index, lambda file: [file.numbered(mention.key)])
    )
    outcome = page_outcome(index, mention, found, "R1q")
    return outcome or make_reference(
        index, mention, ReferenceStatus.NUMBER_MISSING, "R1q", question_targets(corpus, index)
    )


def resolve_question_title(
    corpus: Corpus, index: DocumentIndex, mention: ReferenceMention
) -> Reference | None:
    """R4q: a section title in a log, in the procurement documents of the page.

    None when no procurement document of the page has the heading; the title is
    then looked up as in any other file (R4).
    """
    found = by_page(
        corpus, index, _candidates(corpus, index, lambda file: _titled(file, mention.key))
    )
    return page_outcome(index, mention, found, "R4q")


def _candidates(
    corpus: Corpus,
    index: DocumentIndex,
    lookup: Callable[[DocumentIndex], Iterable[int | None]],
) -> Callable[[str], list[Candidate]]:
    """The sections `lookup` finds on a page: in the file the log names (a), else (b)."""

    def select(page: str) -> list[Candidate]:
        for files in (_named_by_log(corpus, index, page), procurement_files(corpus, index, page)):
            found: list[Candidate] = [
                (sha256, position)
                for sha256 in files
                for position in lookup(corpus.files[sha256])
                if position is not None
            ]
            if found:
                return found
        return []

    return select


def _titled(index: DocumentIndex, text: str) -> tuple[int, ...]:
    match = index.heading_match(text)
    return match[1] if match else ()


def _named_by_log(corpus: Corpus, index: DocumentIndex, page: str) -> list[str]:
    """(a) The files of a page whose link title is the document the log's title names."""
    names = {
        match["document"]
        for link in index.document.links
        if link.page_url == page and (match := _LOG_TITLE.match(normalise_title(link.title)))
    }
    return list(
        dict.fromkeys(
            link.sha256
            for link in corpus.links_on(page)
            if link.sha256 != index.sha256 and normalise_title(link.title) in names
        )
    )


def question_targets(corpus: Corpus, index: DocumentIndex) -> tuple[ReferenceTarget, ...]:
    """The file a log's number or title was searched in, when it is known.

    The file the log's title names, or the page's only procurement document.
    """
    found: dict[str, list[Candidate]] = {}
    for page in corpus.pages_of(index):
        files = _named_by_log(corpus, index, page) or procurement_files(corpus, index, page)
        if len(files) == 1:
            found[page] = [(files[0], None)]
    return targets_by_page(found) if found else ()


def question_date(text: str) -> date | None:
    """When a question was asked: the earliest TendSign time stamp in its section."""
    days = []
    for match in _TIMESTAMP.finditer(text):
        try:
            days.append(date.fromisoformat(match[1]))
        except ValueError:
            continue
    return min(days, default=None)


def settle_by_date(corpus: Corpus, index: DocumentIndex, reference: Reference) -> Reference:
    """(d) An AMBIGUOUS reference of a log, settled among procurement documents by date.

    Each page's candidates are narrowed to those published on or before the
    question date, and the latest of them wins. The reference is returned as it is
    unless every page then has exactly one. Only versions of the tender documents
    are chosen among: with a log or a template among the candidates, the date says
    nothing (20ddb9ebf9cc §18: "Frågor och svar om upphandlingsdokumenten" names
    every tender file of the page; 2 references). Only in a log: the date is that
    of the question. "Upphandlingsdokumenten", the whole tender package, is every
    document published by then, so it is settled only when one was: 50edddbad6c7
    §436 "Det är den svenska versionen enligt upphandlingsdokumenten som ska vara
    signerad" was asked when the Ansökningsinbjudan and the Anbudsinbjudan were
    both out, and stays AMBIGUOUS.
    """
    if reference.status is not ReferenceStatus.AMBIGUOUS or not is_questions_log(index):
        return reference
    if any(
        corpus.type_of(target.sha256) is not DocumentType.PROCUREMENT_DOCUMENT
        for target in reference.targets
    ):
        return reference
    day = question_date(index.sections[reference.mention.section].text)
    if day is None:
        return reference
    pages: dict[str, list[ReferenceTarget]] = {}
    for target in reference.targets:  # all with page_url None when the pages agree
        pages.setdefault(target.page_url or "", []).append(target)
    package = names_tender_package(reference.mention)
    chosen = {
        page: _latest_before(corpus, targets, day, only_one=package)
        for page, targets in pages.items()
    }
    found = {page: [(t.sha256, t.section)] for page, t in chosen.items() if t is not None}
    if len(found) < len(chosen):
        return reference
    return reference.model_copy(
        update={"status": ReferenceStatus.RESOLVED, "targets": targets_by_page(found)}
    )


def _latest_before(
    corpus: Corpus, targets: Sequence[ReferenceTarget], day: date, *, only_one: bool = False
) -> ReferenceTarget | None:
    """The one candidate published on or before `day` and latest; None if not one.

    With `only_one`, the candidate only if no other was published by then.
    """
    kept = []
    for target in targets:
        published = corpus.files[target.sha256].document.metadata.published_on
        if published is None or published <= day:
            kept.append((published, target))
    if len(kept) == 1:
        return kept[0][1]
    if only_one:
        return None
    dated = [(published, target) for published, target in kept if published is not None]
    if not dated or len(dated) < len(kept):
        return None  # nothing published in time, or one whose first version is unknown
    latest = max(published for published, _ in dated)
    winners = [target for published, target in dated if published == latest]
    return winners[0] if len(winners) == 1 else None
