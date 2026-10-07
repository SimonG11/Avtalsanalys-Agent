"""The answer check: the citations, the register facts and the review, in that order.

What:
    `check_answer(draft, ...)` runs every rule on a draft and gives a
    `ChainReport`: the problems for the model, the checked citations and
    register rows, and the reservations the user is told if the draft
    becomes the answer as it is. `agent/middleware.py` decides what happens
    next: a new attempt or the answer.

Why:
    One place says which rules run and in what order (architecture section
    6, ADR 0015), so each rule stays a plain function of what it is given
    (`citations.py`, `register_facts.py`, `review.py`). The deterministic
    rules run first: they are free and exact, and a draft that misquotes a
    section or a date is sent back without paying for a review. The
    reviewer, a second model, runs only on a draft that passed them, and
    only when the draft answers: "it is not in the agreements" has nothing
    for it to weigh against. Everything a rule judges is read again through
    avtal-mcp, never taken from the message history, which a client sends
    and could forge.

How:
    Each cited section is read once, in order, and the citations checked.
    Each agreement in `register_facts` is read once, and the register facts
    checked against those rows, the sections whose citations passed, the
    user's own words and today's date. With no problem so far and
    `answered` true, the reviewer gets the question, the user's answers to
    `ask_user`, the text, the passed sections and the register rows. A
    review that fails (an error, an unreadable verdict) is no problem the
    model could fix: the report says so, and the answer gets a reservation
    instead of a new attempt. The reservations are notes in Swedish, one per
    kind of failure: the sources that failed, the values not found in the
    register, the review's findings, a review that could not be made, and
    an answer with nothing to check it against.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from avtalsagent.agent.register_reader import RegisterEntry, RegisterReader
from avtalsagent.agent.schemas import Citation, FinalAnswer, RegisterFact
from avtalsagent.agent.sections import CitedSection, SectionReader
from avtalsagent.validation.citations import check_citations
from avtalsagent.validation.register_facts import check_register_facts
from avtalsagent.validation.review import (
    REVIEW_FAILED_NOTE,
    AnswerReviewer,
    FollowUp,
    ReviewInput,
    ReviewSource,
    review_passed,
    review_problems,
    review_reservations,
)

NO_SOURCE_NOTE = "Svaret har ingen källa i avtalen eller registret som kunde kontrolleras."
MARKERS_NOTE = "Hänvisningarna [n] i texten stämmer inte med källorna."
NOT_REVIEWED_NOTE = (
    "Svaret granskades inte mot källorna, eftersom det inte klarade kontrollen av citat och "
    "registeruppgifter."
)


@dataclass(frozen=True)
class ChainReport:
    """The outcome of the check: no problems means the draft passed every rule run."""

    problems: list[str]  # in Swedish, for the model's new attempt
    citations: list[Citation]  # every source of the draft, each marked verified or not
    register_facts: list[RegisterFact]  # the rows of the declared agreements the register has
    reservations: list[str]  # in Swedish, for the user, if the draft is given as it is
    review_failed: bool  # the reviewer gave no verdict; no new attempt can fix that
    has_source: bool  # a citation passed, or a register fact was checked


async def check_answer(
    draft: FinalAnswer,
    *,
    sections: SectionReader,
    register: RegisterReader,
    reviewer: AnswerReviewer,
    question: str,
    follow_ups: Sequence[FollowUp],
    today: date,
) -> ChainReport:
    """Run the rules on `draft` (see the module's How)."""
    read: dict[tuple[str, int], CitedSection | None] = {}
    for source in draft.citations:
        key = (source.sha256, source.section_position)
        if key not in read:
            read[key] = await sections.read(*key)
    cited = check_citations(draft, read)
    passed = _passed_sections(draft, cited.citations, read)

    entries: dict[str, list[RegisterEntry] | None] = {}
    for number in draft.register_facts:
        if number not in entries:
            entries[number] = await register.read(number)
    user_texts = [question, *(follow_up.answer for follow_up in follow_ups)]
    facts = check_register_facts(draft, entries, passed, user_texts, today)

    problems = [*cited.problems, *facts.problems]
    reservations = _source_reservations(cited.citations)
    # check_citations gives one problem per failed source; the rest are the [n] markers'.
    if len(cited.problems) > sum(not citation.verified for citation in cited.citations):
        reservations.append(MARKERS_NOTE)
    if facts.unbacked:
        reservations.append(
            "Kunde inte kontrolleras mot registret: " + ", ".join(facts.unbacked) + "."
        )
    has_source = bool(passed) or bool(facts.facts)
    if draft.answered and not has_source and not problems:  # else a note says what failed
        reservations.append(NO_SOURCE_NOTE)

    review_failed = False
    if problems and draft.answered:
        reservations.append(NOT_REVIEWED_NOTE)
    elif draft.answered:
        verdict = await reviewer.review(
            ReviewInput(
                question=question,
                follow_ups=list(follow_ups),
                answer_text=draft.text,
                sources=_review_sources(draft, read, cited.citations),
                register_facts=facts.facts,
                today=today,
            )
        )
        if verdict is None:
            review_failed = True
            reservations.append(REVIEW_FAILED_NOTE)
        elif not review_passed(verdict):
            problems += review_problems(verdict)
            reservations += review_reservations(verdict)

    return ChainReport(
        problems=problems,
        citations=cited.citations,
        register_facts=facts.facts,
        reservations=reservations,
        review_failed=review_failed,
        has_source=has_source,
    )


def _passed_sections(
    draft: FinalAnswer,
    citations: Sequence[Citation],
    read: dict[tuple[str, int], CitedSection | None],
) -> list[CitedSection]:
    """The sections of the citations that passed, each once, in the draft's order."""
    passed: list[CitedSection] = []
    for source, citation in zip(draft.citations, citations, strict=True):
        section = read.get((source.sha256, source.section_position))
        if citation.verified and section is not None and section not in passed:
            passed.append(section)
    return passed


def _review_sources(
    draft: FinalAnswer,
    read: dict[tuple[str, int], CitedSection | None],
    citations: Sequence[Citation],
) -> list[ReviewSource]:
    """Each passed source with its whole section, as the reviewer reads it; a section once."""
    sources = []
    first: dict[tuple[str, int], int] = {}  # each section's first source id
    for source, citation in zip(draft.citations, citations, strict=True):
        key = (source.sha256, source.section_position)
        section = read.get(key)
        if citation.verified and section is not None:
            same_as = first.setdefault(key, source.id)
            sources.append(
                ReviewSource(
                    id=source.id,
                    file_title=section.file_title,
                    page_titles=section.page_titles,
                    section_number=section.section_number,
                    section_title=section.section_title,
                    text=section.text if same_as == source.id else "",
                    same_as=same_as if same_as != source.id else None,
                )
            )
    return sources


def _source_reservations(citations: Sequence[Citation]) -> list[str]:
    """A note naming the sources whose quotes could not be checked, if any."""
    failed = [f"[{citation.id}]" for citation in citations if not citation.verified]
    if not failed:
        return []
    named = ", ".join(failed)
    if len(failed) == 1:
        return [f"Källa {named} kunde inte kontrolleras mot avtalstexten."]
    return [f"Källorna {named} kunde inte kontrolleras mot avtalstexten."]
