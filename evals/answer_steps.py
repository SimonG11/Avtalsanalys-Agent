"""The agent's way through one question: its tool calls, and why the check sent drafts back (M11).

What:
    `read_steps(messages)` reads a run's messages into `Step`s: every tool
    call the agent made, in order, with its arguments, whether the tool
    answered with an error, what became of a draft (`FinalAnswer`), and,
    for `read_section`, whether its section was named by an earlier result
    (`target_from`) and whether an earlier search had returned it
    (`found_by_search`). `read_rejections` gives each draft the answer check
    sent back as a `Rejection`: its problems, each with the rule that found
    it (`problem_rule`). `rule_counts` counts a question's rejections by rule.

Why:
    The number of tool calls says how much the agent did, not what it
    decided: which tool next and with what, whether it read a section a
    reference pointed to, when it asked the user, and what the check made it
    change. The run's messages hold all of it, so it is read from them after
    the run, and nothing in the agent changes for the measurement.

How:
    The calls are the AI messages' tool calls in order; a call's result is
    the tool message with its id. A tool's structured result is the MCP
    adapter's artifact (`structured_content`), else its text read as JSON.
    A `read_section` call's section is its file and position, or its file
    and number when no position is given (stripped of a final dot, as the
    tool does). It is from a reference when a section target (one with a
    position; a whole file does not count) under `references` in an
    earlier successful result of `read_section` or `resolve_reference` is
    that section; earlier means a tool message before the AI message that
    made the call. Else it is from an amendment when the section is the
    `amending` section of an earlier `find_amendments` result. The target
    may have been a search hit as well: this says what the agent had in
    front of it, not why it chose the section. So a read also notes whether
    the section was a hit, or a hit's copy (`copies`), in an earlier
    successful `search_documents` result: a read from a reference that no
    search had returned is one the agent can only have taken from the
    reference (or from an outline). A position is a whole number, or a
    string of at most nine of the digits 0-9; any other value names no
    section, so a malformed argument never stops the reading. A draft is sent back when its
    tool result has status error, submitted when it is ToolStrategy's
    ANSWER_SUBMITTED, and refused (its form) otherwise, as `answer_run`
    counts them. The check's feedback replaces the draft's tool result as
    `AnswerCheck` writes it: a line "Kontrollen underkände svaret (…):", a
    line "- <problem>" per problem, and FIX_INSTRUCTION. Each problem goes
    to the rule whose wording it starts with (`validation/citations.py`,
    `register_facts.py`, `latest_wording.py`, `review.py`); the tests hold
    each rule's own problems to it, so a reworded rule fails a test instead
    of being miscounted. A problem no rule fits, or a feedback of another
    form, is "unknown".
"""

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

from avtalsagent.agent.graph import ANSWER_SUBMITTED
from avtalsagent.agent.middleware import FINAL_ANSWER_TOOL, FIX_INSTRUCTION

READ_SECTION = "read_section"
RESOLVE_REFERENCE = "resolve_reference"
SEARCH_DOCUMENTS = "search_documents"
# The tools whose results list a section's references, and the one that lists amendments.
REFERENCE_TOOLS = (READ_SECTION, RESOLVE_REFERENCE)
FIND_AMENDMENTS = "find_amendments"

DraftOutcome = Literal["sent_back", "submitted", "refused"]
TargetSource = Literal["reference", "amendment"]
CheckRule = Literal["citations", "register_facts", "latest_wording", "review", "unknown"]
CHECK_RULES: tuple[CheckRule, ...] = (
    "citations",
    "register_facts",
    "latest_wording",
    "review",
    "unknown",
)


@dataclass(frozen=True)
class Step:
    """One tool call of the agent, in the order it was made."""

    name: str
    args: Mapping[str, Any]
    error: bool = False  # the tool answered with status error; never set for a draft
    draft: DraftOutcome | None = None  # FinalAnswer only; None without a result
    target_from: TargetSource | None = None  # read_section only: what named its section
    found_by_search: bool = False  # read_section only: an earlier search returned its section


@dataclass(frozen=True)
class Problem:
    """One line of the check's feedback, and the rule that wrote it."""

    rule: CheckRule
    text: str


@dataclass(frozen=True)
class Rejection:
    """A draft the answer check sent back, with what it found wrong."""

    problems: tuple[Problem, ...]

    @property
    def rules(self) -> tuple[CheckRule, ...]:
        """The rules that found a problem, each once, in the feedback's order."""
        return tuple(dict.fromkeys(problem.rule for problem in self.problems))


# --- The calls --------------------------------------------------------------------------------

# A section as a call names it, or as a result lists it: by position, or by number.
_Place = tuple[str, str, int | str]


def read_steps(messages: Sequence[BaseMessage]) -> tuple[Step, ...]:
    """Every tool call in `messages`, in order, with its result's outcome (see the How)."""
    results = {m.tool_call_id: m for m in messages if isinstance(m, ToolMessage)}
    referenced: set[_Place] = set()
    amending: set[_Place] = set()
    searched: set[_Place] = set()
    steps: list[Step] = []
    for message in messages:
        if isinstance(message, ToolMessage):
            _note_targets(message, referenced, amending, searched)
            continue
        if not isinstance(message, AIMessage):
            continue
        for call in message.tool_calls:
            name, args = call["name"], dict(call["args"])
            result = results.get(call["id"] or "")
            if name == FINAL_ANSWER_TOOL:
                steps.append(Step(name, args, draft=_draft(result)))
                continue
            target_from: TargetSource | None = None
            found_by_search = False
            if name == READ_SECTION and (place := _called_place(args)) is not None:
                if place in referenced:
                    target_from = "reference"
                elif place in amending:
                    target_from = "amendment"
                found_by_search = place in searched
            error = result is not None and result.status == "error"
            steps.append(
                Step(
                    name,
                    args,
                    error=error,
                    target_from=target_from,
                    found_by_search=found_by_search,
                )
            )
    return tuple(steps)


def structured_result(message: ToolMessage) -> Mapping[str, Any] | None:
    """A tool's result as data: the MCP adapter's artifact, else its text read as JSON."""
    artifact = message.artifact
    if isinstance(artifact, Mapping):
        content = artifact.get("structured_content")
        if isinstance(content, Mapping):
            return content
    try:
        data = json.loads(message.text)
    except ValueError:
        return None
    return data if isinstance(data, Mapping) else None


def _draft(result: ToolMessage | None) -> DraftOutcome | None:
    if result is None:
        return None
    if result.status == "error":
        return "sent_back"
    return "submitted" if result.text == ANSWER_SUBMITTED else "refused"


def _note_targets(
    message: ToolMessage, referenced: set[_Place], amending: set[_Place], searched: set[_Place]
) -> None:
    """Add the sections a successful result names: its references' targets, amendments or hits."""
    if message.status == "error" or message.name not in (
        *REFERENCE_TOOLS,
        FIND_AMENDMENTS,
        SEARCH_DOCUMENTS,
    ):
        return
    result = structured_result(message)
    if result is None:
        return
    if message.name == FIND_AMENDMENTS:
        for amendment in _items(result.get("amendments")):
            amending.update(_places(amendment.get("amending")))
        return
    if message.name == SEARCH_DOCUMENTS:
        for hit in _items(result.get("hits")):
            searched.update(_places(hit))
            for copy in _items(hit.get("copies")):
                searched.update(_places(copy))
        return
    for reference in _items(result.get("references")):
        for target in _items(reference.get("targets")):
            referenced.update(_places(target))


def _places(section: Any) -> list[_Place]:
    """A listed section by position and by number; none for a whole file (no position)."""
    if not isinstance(section, Mapping) or not isinstance(section.get("sha256"), str):
        return []
    position = _position(section.get("section_position"))
    if position is None:
        return []
    places: list[_Place] = [(section["sha256"], "position", position)]
    if (number := _number(section.get("section_number"))) is not None:
        places.append((section["sha256"], "number", number))
    return places


def _called_place(args: Mapping[str, Any]) -> _Place | None:
    """The section a read_section call asks for: by position if given, else by number."""
    sha256 = args.get("sha256")
    if not isinstance(sha256, str):
        return None
    if (position := _position(args.get("section_position"))) is not None:
        return (sha256, "position", position)
    if (number := _number(args.get("section_number"))) is not None:
        return (sha256, "number", number)
    return None


_DIGITS = re.compile(r"[0-9]{1,9}")  # no file has a billion sections; int() refuses 4 300 digits


def _position(value: Any) -> int | None:
    """A section position: a whole number, or a string of the digits 0-9 (not "²" or "①")."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and _DIGITS.fullmatch(text := value.strip()):
        return int(text)
    return None


def _number(value: Any) -> str | None:
    """A section number as read_section compares it: without surrounding space or a final dot."""
    if not isinstance(value, str):
        return None
    return value.strip().rstrip(".") or None


def _items(value: Any) -> list[Mapping[str, Any]]:
    return [item for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []


# --- The check's rejections -------------------------------------------------------------------

_FEEDBACK_HEADER = re.compile(r"Kontrollen underkände svaret \([^)\n]*\):\n")
# How each rule's problems begin, as the rules write them; the first that matches wins.
_RULE_STARTS: tuple[tuple[CheckRule, re.Pattern[str]], ...] = (
    # "Källa [1] (Allmänna villkor, avsnitt 6.21.9 Uppsägning) har ändrats av …"
    ("latest_wording", re.compile(r"Källa \[\d+\] \(.*\) har ändrats av ", re.DOTALL)),
    # The [n] markers and ids, and each source's section and quote.
    (
        "citations",
        re.compile(
            r"Hänvisningen \[\d+\] i texten |Källa \[\d+\](?::| används inte i texten)"
            r"|Flera källor har id |Källornas id ska vara "
        ),
    ),
    # The declared agreements, and the numbers and dates in the text.
    (
        "register_facts",
        re.compile(r"(?:Avtalsnumret|Avtalet|Numret|Organisationsnumret|Datumet|Uträkningen) "),
    ),
    # A claim in quote marks (or "Ett påstående" for one without words), and what is missing.
    ("review", re.compile(r"”|Ett påstående|Saknas: ")),
)


def read_rejections(messages: Sequence[BaseMessage]) -> tuple[Rejection, ...]:
    """Each draft the check sent back, in order, with its feedback's problems."""
    return tuple(
        Rejection(feedback_problems(message.text))
        for message in messages
        if isinstance(message, ToolMessage)
        and message.name == FINAL_ANSWER_TOOL
        and message.status == "error"
    )


def feedback_problems(feedback: str) -> tuple[Problem, ...]:
    """The problems in the check's feedback, one per "- " line, each with its rule."""
    header = _FEEDBACK_HEADER.match(feedback)
    if header is None:  # not the check's feedback: kept whole, its rule unknown
        return (Problem("unknown", feedback.strip()),)
    body = feedback[header.end() :].removesuffix(FIX_INSTRUCTION).strip("\n")
    lines: list[str] = []
    for line in body.split("\n"):
        if line.startswith("- ") or not lines:
            lines.append(line.removeprefix("- "))
        else:  # a problem whose text has a line break
            lines[-1] += "\n" + line
    return tuple(Problem(problem_rule(line), line) for line in lines if line.strip())


def problem_rule(problem: str) -> CheckRule:
    """The rule that wrote `problem`, by how it begins; "unknown" when none does."""
    for rule, start in _RULE_STARTS:
        if start.match(problem):
            return rule
    return "unknown"


def rule_counts(rejections: Sequence[Rejection]) -> dict[CheckRule, int]:
    """How many of the drafts each rule sent back, in CHECK_RULES' order; a draft per rule once."""
    counts = {rule: 0 for rule in CHECK_RULES}
    for rejection in rejections:
        for rule in rejection.rules:
            counts[rule] += 1
    return {rule: count for rule, count in counts.items() if count}
