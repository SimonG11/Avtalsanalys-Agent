"""The review: a second model judges whether the answer follows from its sources.

What:
    `ReviewVerdict` and `ClaimReview`, the reviewer's structured judgement;
    `ReviewInput`, `ReviewSource` and `FollowUp`, what it reads; `AnswerReviewer`, the
    reviewer (`agent/reviewer.py` asks the model). From a verdict,
    `review_passed` decides whether the answer passed, `review_problems`
    gives the lines of the agent's feedback and `review_reservations` the
    notes the user sees. `REVIEW_FAILED_NOTE` is the note when there is no
    verdict.

Why:
    The citation and register checks prove that the quoted words and the
    register facts exist. They cannot prove that the answer says what the
    sources say (three months where the section says six), or that it
    answers the whole question. That takes judgement, so a separate model,
    deliberately not the agent's, gives it (architecture layer 5, ADR 0015).
    The model only judges; whether the answer passes, what the agent is
    told and what the user sees are decided here, in code.

How:
    An answer passes when every claim is "supported" and nothing is
    missing; a verdict without claims passes. The feedback has one line per
    claim that is not supported, in the verdict's order: the claim, its
    sources [n] and the model's reason, so a new attempt can fix exactly
    that claim; then one line per missing item. The user's notes name the
    doubted claims and the missing items, at most `NOTE_ITEMS` of each, cut
    at a word, so the reservation stays a line or two. The model's texts
    are put on one line (whitespace collapsed) before they are used.
"""

import re
from datetime import date
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from avtalsagent.agent.schemas import RegisterFact

Support = Literal["supported", "partly", "unsupported"]


class ClaimReview(BaseModel):
    """Ett påstående i svaret och hur väl källorna stöder det."""

    claim: str = Field(description="Påståendet, med svarets egna ord, kort.")
    citation_ids: list[int] = Field(
        description="Numren [n] på de källor som svaret anger för påståendet; tom om inga."
    )
    support: Support = Field(
        description=(
            "supported: källorna eller registret säger detta. partly: bara en del av det, "
            "eller med ett villkor som svaret utelämnar. unsupported: källorna säger inte detta "
            "eller säger något annat."
        )
    )
    reason: str = Field(
        description="Varför, kort och konkret: vad källan säger i stället eller vad som saknas."
    )


class ReviewVerdict(BaseModel):
    """Granskningen av svaret: varje påstående för sig, och det väsentliga som saknas."""

    claims: list[ClaimReview] = Field(description="Svarets påståenden, i svarets ordning.")
    missing: list[str] = Field(
        description=(
            "Det som frågan gäller men som svaret inte tar upp, fast källorna eller registret "
            "besvarar det. Tom om inget väsentligt saknas."
        )
    )


class ReviewSource(BaseModel):
    """A cited section as the reviewer reads it: re-read through avtal-mcp, never the history."""

    id: int  # the [n] of the answer
    file_title: str
    # The agreement pages that link to the file: which agreements the text belongs to.
    page_titles: list[str] = Field(default_factory=list)
    section_number: str | None
    section_title: str
    text: str  # empty when `same_as` is set
    # The [n] of an earlier source with the same section, whose text is not sent twice.
    same_as: int | None = None


class FollowUp(BaseModel):
    """A question the agent asked the user with `ask_user`, and the user's answer."""

    question: str
    answer: str


class ReviewInput(BaseModel):
    """What the reviewer reads: the question, the answer and what it rests on."""

    question: str
    follow_ups: list[FollowUp]  # the agent's questions to the user after the question
    answer_text: str
    sources: list[ReviewSource]
    register_facts: list[RegisterFact]
    today: date


class AnswerReviewer(Protocol):
    """Reviews an answer that passed the deterministic checks."""

    async def review(self, request: ReviewInput) -> ReviewVerdict | None:
        """The verdict, or None when the review failed (an error, an unreadable verdict)."""
        ...


# The user's note when the review gave no verdict; the answer is then given with reservation.
REVIEW_FAILED_NOTE = "Svaret kunde inte granskas."

# A note to the user names at most this many claims, and as many missing items, each cut to
# NOTE_ITEM_LENGTH characters. The feedback to the agent quotes a claim up to
# FEEDBACK_CLAIM_LENGTH.
NOTE_ITEMS = 3
NOTE_ITEM_LENGTH = 100
FEEDBACK_CLAIM_LENGTH = 300

# How the feedback states a claim's support; a supported claim is not named.
_VERDICT: dict[Support, str] = {"partly": "stöds bara delvis", "unsupported": "stöds inte"}
# Quote marks the model may put around a claim; the texts here add their own.
_QUOTE_MARKS = "\"'“”„«»‘’"
_WHITESPACE = re.compile(r"\s+")


def review_passed(verdict: ReviewVerdict) -> bool:
    """Whether the answer passed the review: every claim supported and nothing missing."""
    return all(claim.support == "supported" for claim in verdict.claims) and not _missing(verdict)


def review_problems(verdict: ReviewVerdict) -> list[str]:
    """The review's problems, in Swedish for the agent: one line per claim and missing item."""
    problems = []
    for claim in verdict.claims:
        if claim.support == "supported":
            continue
        quoted = _quoted(claim.claim, FEEDBACK_CLAIM_LENGTH) or "Ett påstående"
        line = f"{quoted}{_markers(claim.citation_ids)}: {_VERDICT[claim.support]}."
        if reason := _sentence(claim.reason):
            line = f"{line} {reason}"
        problems.append(line)
    problems.extend(f"Saknas: {_sentence(item)}" for item in _missing(verdict))
    return problems


def review_reservations(verdict: ReviewVerdict) -> list[str]:
    """The review's notes, in Swedish for the user: what lacks support and what may be missing.

    At most two notes (the answer's reservations are at most three lines).
    """
    notes = []
    doubted = [
        _quoted(claim.claim, NOTE_ITEM_LENGTH) or "ett påstående"
        for claim in verdict.claims
        if claim.support != "supported"
    ]
    if doubted:
        listed = _listing(doubted, ", ", ("ett påstående", "påståenden"))
        notes.append(f"Granskningen fann inte fullt stöd i källorna för: {listed}.")
    if missing := [_shorten(item.rstrip("."), NOTE_ITEM_LENGTH) for item in _missing(verdict)]:
        listed = _listing(missing, "; ", ("en punkt", "punkter"))
        notes.append(_sentence(f"Svaret kan vara ofullständigt: {listed}"))
    return notes


def _missing(verdict: ReviewVerdict) -> list[str]:
    """The verdict's missing items on one line each, the empty ones left out."""
    return [text for item in verdict.missing if (text := _one_line(item))]


def _one_line(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip()


def _sentence(text: str) -> str:
    """`text` on one line, ending with a full stop unless it ends a sentence already."""
    text = _one_line(text)
    return text if not text or text.endswith((".", "!", "?", "…")) else f"{text}."


def _quoted(claim: str, length: int) -> str:
    """The claim in Swedish quote marks, without its full stop, cut to `length`; empty if none."""
    text = _one_line(claim).strip(_QUOTE_MARKS).strip().rstrip(".").rstrip()
    return f"”{_shorten(text, length)}”" if text else ""


def _shorten(text: str, length: int) -> str:
    """`text` cut at a word to at most `length` characters, an ellipsis marking the cut."""
    if len(text) <= length:
        return text
    cut = text[: length - 1]
    if (space := cut.rfind(" ")) > length // 2:
        cut = cut[:space]
    return f"{cut.rstrip(' ,;:.')}…"


def _markers(citation_ids: list[int]) -> str:
    """The sources as the answer's text writes them, " [1][2]"; empty without sources."""
    markers = "".join(f"[{n}]" for n in dict.fromkeys(citation_ids))
    return f" {markers}" if markers else ""


def _listing(items: list[str], separator: str, more: tuple[str, str]) -> str:
    """At most `NOTE_ITEMS` of `items` as a Swedish list ("a, b och c"), and how many more.

    `more` is the noun for one more item, with its article, and for several.
    """
    parts = items[:NOTE_ITEMS]
    if rest := len(items) - len(parts):
        parts.append(f"{more[0]} till" if rest == 1 else f"{rest} {more[1]} till")
    return parts[0] if len(parts) == 1 else f"{separator.join(parts[:-1])} och {parts[-1]}"
