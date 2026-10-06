"""Ingestion step 4: resolve each reference to the file and section it points at.

What:
    `resolve` takes all files of the corpus (`CorpusDocument`: metadata, sections,
    mentions and links) and gives one `Reference` per mention: its status, the
    rule that decided it, and its targets. `resolution_rate` is the share of
    references that resolve, the measure M4 reports ("andelen upplösta
    hänvisningar är mätt").

Why:
    The agent must be able to follow "enligt punkt 6.21" or "bilaga Priser" to
    the text it points at, and a reviewer must be able to check each step. The
    rules are split by where they look: the file itself
    (`resolve_in_document`), the other files of the agreement page
    (`resolve_on_page`), and the procurement documents a questions-and-answers
    log is about (`resolve_questions`).

How:
    A mention whose text already decides it keeps its status, with rule None:
    LAW (EXTERNAL), PLACEHOLDER, LIST_ITEM, and SELF ("dessa Allmänna villkor").
    The others, by kind, in this order:

    | kind           | rules, in order                                          |
    |----------------|----------------------------------------------------------|
    | SECTION_NUMBER | R1x when a document is named with it; R1q in a log;      |
    |                | else R1 (same file)                                      |
    | SECTION_TITLE  | R4q in a log; R4 in the same file, then in the other    |
    |                | files of the page; else TITLE_MISSING                    |
    | ANNEX_NUMBER   | R3                                                       |
    | ANNEX_NAME     | R5                                                       |
    | DOCUMENT       | R2                                                       |
    | QUESTION       | RQ (same log)                                            |

    In a log, an AMBIGUOUS choice among procurement documents is then settled by
    the question's date (`resolve_questions.settle_by_date`). `Reference.rule` is
    the rule that decided the status, whatever the status: "R1q" with
    NUMBER_MISSING says that rule searched and found nothing.

    The rate (ADR 0009 decision 9) counts every reference except laws and
    questions ("fråga N") and those with status LIST_ITEM, SELF or EXTERNAL:
    RESOLVED / that count. NOT_PUBLISHED stays in the count: the target exists
    but is not on the page, which the rate should show. SELF is a document
    naming itself; a reference that resolves to its own section (11db2f3d1852
    §6.15: "gäller detta avsnitt Leverans och leveranskontroll", 73 in the
    pilot) is RESOLVED and counts, since it found the section it names.

    Not built (each covers fewer than 50 references in the pilot, about 1.2
    percentage points of the rate together): a title followed by a document
    ("avsnitt X i Allmänna villkor", R4x, 39), "kapitel <Dok>" where the file has
    no such chapter (46), a unique heading that starts with the reference's
    phrase (R4's third tier, 33), a number found by its suffix (1), and R1q's
    number + title step (46).
"""

from collections.abc import Iterable, Sequence
from typing import assert_never

from avtalsagent.domain.extracted import (
    Reference,
    ReferenceKind,
    ReferenceMention,
    ReferenceStatus,
)
from avtalsagent.ingestion.extract.resolve_in_document import (
    CorpusDocument,
    DocumentIndex,
    make_reference,
    own_targets,
    resolve_number,
    resolve_title,
    title_not_found,
)
from avtalsagent.ingestion.extract.resolve_on_page import (
    Corpus,
    build_corpus,
    resolve_annex_name,
    resolve_annex_number,
    resolve_document,
    resolve_named_number,
    resolve_title_on_page,
)
from avtalsagent.ingestion.extract.resolve_questions import (
    is_questions_log,
    question_targets,
    resolve_question,
    resolve_question_number,
    resolve_question_title,
    settle_by_date,
)

__all__ = ["CorpusDocument", "counts_in_rate", "resolution_rate", "resolve"]

# Not references to a section or document of the corpus, so not in the rate.
_KINDS_NOT_COUNTED = frozenset((ReferenceKind.LAW, ReferenceKind.QUESTION))
_STATUSES_NOT_COUNTED = frozenset(
    (ReferenceStatus.LIST_ITEM, ReferenceStatus.SELF, ReferenceStatus.EXTERNAL)
)


def resolve(documents: Sequence[CorpusDocument]) -> list[Reference]:
    """One reference per mention, in the order of the documents and their mentions."""
    corpus = build_corpus(documents)
    references = []
    for document in documents:
        index = corpus.files[document.sha256]
        for mention in document.mentions:
            reference = _resolve(corpus, index, mention)
            references.append(settle_by_date(corpus, index, reference))
    return references


def _resolve(corpus: Corpus, index: DocumentIndex, mention: ReferenceMention) -> Reference:
    if mention.status is not None:
        return make_reference(index, mention, mention.status, None)
    match mention.kind:
        case ReferenceKind.SECTION_NUMBER:
            if mention.document_name:
                return resolve_named_number(corpus, index, mention, mention.document_name)
            if is_questions_log(index):
                return resolve_question_number(corpus, index, mention)
            return resolve_number(index, mention)
        case ReferenceKind.SECTION_TITLE:
            return _resolve_title(corpus, index, mention)
        case ReferenceKind.ANNEX_NUMBER:
            return resolve_annex_number(corpus, index, mention)
        case ReferenceKind.ANNEX_NAME:
            return resolve_annex_name(corpus, index, mention)
        case ReferenceKind.DOCUMENT:
            return resolve_document(corpus, index, mention)
        case ReferenceKind.QUESTION:
            return resolve_question(index, mention)
        case ReferenceKind.LAW:
            return make_reference(index, mention, ReferenceStatus.EXTERNAL, None)
        case _:
            assert_never(mention.kind)


def _resolve_title(corpus: Corpus, index: DocumentIndex, mention: ReferenceMention) -> Reference:
    """R4q in a log, then R4 in the file itself and in the other files of the page."""
    log = is_questions_log(index)
    if log and (reference := resolve_question_title(corpus, index, mention)):
        return reference
    reference = resolve_title(index, mention) or resolve_title_on_page(corpus, index, mention)
    if reference:
        return reference
    searched = question_targets(corpus, index) if log else own_targets(index, None)
    return title_not_found(index, mention, searched)


def counts_in_rate(reference: Reference) -> bool:
    """Whether a reference is in the denominator of `resolution_rate`."""
    return (
        reference.mention.kind not in _KINDS_NOT_COUNTED
        and reference.status not in _STATUSES_NOT_COUNTED
    )


def resolution_rate(references: Iterable[Reference]) -> float:
    """RESOLVED / references in the rate (`counts_in_rate`); 0.0 when there are none."""
    counted = [reference for reference in references if counts_in_rate(reference)]
    if not counted:
        return 0.0
    resolved = sum(1 for reference in counted if reference.status is ReferenceStatus.RESOLVED)
    return resolved / len(counted)
