"""Scores of the agent's answers against the gold, and their summaries (M11).

What:
    `score(question, run, judgement, judged_by)` makes a `QuestionResult`:
    the run with its citations' places (`cited_places`), which of the
    gold's document sources the answer cites with a checked quote
    (`sources_found`), how many of the gold's agreements its register facts
    cover (`register_score`), and the verdict. `rule_judgement` judges an
    answer that needs no judge (`no_draft`). `summarize` gives the `Summary` of any group
    of results, and `by_category` the groups by category. `summarize_paths`
    gives the `PathSummary` of how the agent went about them: the calls per
    tool, its model calls, the sections it read from a reference (and of
    those, the ones no earlier search had returned), and the check's
    rejections by rule. `verdict_score` scores a verdict (correct 1,
    partly correct 0.5, incorrect 0), as the runs are compared
    (`compare_answer_runs`). For a gold question that says whether the
    agent should ask the user (`should_ask`), a result also has the ask
    judge's verdict (`ask_judge`), whether the agent asked as it should
    (`asked_right`) and whether it asked without need (`unnecessary_ask`);
    `summarize_asks` gives their `AskSummary`, of the results or of any rows
    with the same values (`AskOutcome`: the comparison of runs reads them
    from the JSON reports).

Why:
    The report's numbers come from plain values, so they can be tested
    without a model or a server, and one summary serves the whole run and
    each category.

How:
    The checked answer's citations have the file and the section number,
    but not the position that a section without a number is known by; the
    draft has it under the same citation id, and it is used when the
    draft's citation is in the same file. A gold alternative matches a
    citation in the same file at the same position, or with the same number
    when either position is unknown. Only citations whose quote the check
    verified count. Agreements are compared by `agreement_key`, as the
    register's are. A run that ended without a draft (NO_DRAFT_TEXT) is
    incorrect by rule. Percentiles are nearest-rank. The path's numbers
    count the questions whose path was saved (`QuestionRun.path_saved`):
    every question but one that never reached the graph, which is counted
    as not saved instead of as no calls. An ask is right for a question
    that should ask only when the agent asked and the ask judge found that
    its question separates the expected options; one the ask judge did not
    judge (no judge, or no verdict) is neither right nor wrong (None). For a
    question that should not ask, not asking is right. A run that saved
    nothing (no state of the graph, and no question to the user) does not
    show whether the agent asked (`could_ask` is false): it is neither right
    nor wrong, nor an unnecessary ask, and it is in neither count of the
    `AskSummary`, so an error is not counted as "did not ask". A run that
    never asks (the fixed workflow) scores 0 of the questions that should
    ask.
"""

import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol

from avtalsagent.agent.middleware import NO_DRAFT_TEXT
from avtalsagent.agent.schemas import Answer, AnswerStatus, FinalAnswer
from avtalsagent.domain.identifiers import agreement_key
from evals.answer_run import QuestionRun, TokenUse, cost_range, total_use
from evals.answer_steps import CHECK_RULES, READ_SECTION, CheckRule, Step, rule_counts
from evals.ask_judge import AskJudgement
from evals.gold import Alternative, DocumentSource, GoldQuestion, RegisterSource
from evals.judge import Judgement, Verdict

# The reason given to an answer judged by rule: the run ended without a draft.
NO_DRAFT_REASON = "Agenten kom inte fram till ett svar inom gränsen för modellanrop."

VERDICTS: tuple[Verdict, ...] = ("correct", "partly_correct", "incorrect")
STATUSES: tuple[AnswerStatus, ...] = ("verified", "with_reservation", "no_answer")
JudgedBy = Literal["judge", "rule"]
# The right-score of a verdict: what the runs' comparison averages.
VERDICT_SCORES: Mapping[Verdict, float] = {"correct": 1.0, "partly_correct": 0.5, "incorrect": 0.0}

# --- Scoring (pure) ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CitedPlace:
    """Where a citation of the answer points, and whether its quote passed the check."""

    sha256: str
    position: int | None  # from the draft; None when the draft does not give it
    number: str | None
    verified: bool


@dataclass(frozen=True)
class QuestionResult:
    """One gold question, the agent's answer and its scores."""

    id: str
    category: str
    answerable: bool
    difficulty: int
    question: str
    gold_answer: str
    run: QuestionRun
    places: tuple[CitedPlace, ...]
    document_sources: int
    sources_found: tuple[bool, ...]  # per document source of the gold, in its order
    register_required: int  # the gold's agreements, as keys
    register_found: int
    register_extra: int  # agreements the answer declares that the gold does not name
    judgement: Judgement | None
    judged_by: JudgedBy | None
    judge_usage: Mapping[str, TokenUse] = field(default_factory=dict)  # both judges' calls
    should_ask: bool | None = None  # the gold's; None when it does not say
    expected_options: tuple[str, ...] = ()  # the gold's options for the agent's question
    ask_judgement: AskJudgement | None = None  # None unless it should ask, asked and was judged

    @property
    def asked(self) -> bool:
        """Whether the agent asked the user at least once."""
        return bool(self.run.asked)

    @property
    def asked_right(self) -> bool | None:
        """Asked when it should, with a question that separates; did not when it should not.

        None when the gold does not say, for an ask the ask judge did not judge, and
        for a run that saved nothing (`could_ask`).
        """
        if self.should_ask is None or not self.could_ask:
            return None
        if not self.should_ask:
            return not self.asked
        if not self.asked:
            return False
        return self.ask_judgement.separates if self.ask_judgement is not None else None

    @property
    def could_ask(self) -> bool:
        """Whether the run shows if the agent asked: its graph's state was saved, or it asked.

        False for a run that saved nothing, as when the session to avtal-mcp never opened.
        """
        return self.run.path_saved or self.asked

    @property
    def unnecessary_ask(self) -> bool | None:
        """Asked although the gold says it should not; None as for `asked_right`."""
        if self.should_ask is None or not self.could_ask:
            return None
        return self.asked and not self.should_ask

    @property
    def status(self) -> AnswerStatus | None:
        return self.run.answer.status if self.run.answer is not None else None

    @property
    def verdict(self) -> Verdict | None:
        return self.judgement.verdict if self.judgement is not None else None

    @property
    def citations(self) -> int:
        return len(self.places)

    @property
    def verified_citations(self) -> int:
        return sum(1 for place in self.places if place.verified)

    @property
    def cost(self) -> tuple[float, float] | None:
        """The run's cost in dollars, low and high (`cost_range`)."""
        return cost_range(self.run.usage)


def cited_places(answer: Answer | None, draft: FinalAnswer | None) -> tuple[CitedPlace, ...]:
    """The answer's citations with each section's position from the draft, by citation id."""
    if answer is None:
        return ()
    drafted = {citation.id: citation for citation in draft.citations} if draft else {}
    places = []
    for citation in answer.citations:
        source = drafted.get(citation.id)
        position = (
            source.section_position
            if source is not None and source.sha256 == citation.sha256
            else None
        )
        places.append(
            CitedPlace(citation.sha256, position, citation.section_number, citation.verified)
        )
    return tuple(places)


def is_alternative(alternative: Alternative, place: CitedPlace) -> bool:
    """The citation points at the gold's alternative: the same file and position (or number)."""
    if alternative.sha256 != place.sha256:
        return False
    if alternative.section_position is not None and place.position is not None:
        return alternative.section_position == place.position
    return alternative.section_number is not None and alternative.section_number == place.number


def sources_found(
    sources: Sequence[DocumentSource], places: Sequence[CitedPlace]
) -> tuple[bool, ...]:
    """For each source, whether a citation with a checked quote points at one of its places."""
    verified = [place for place in places if place.verified]
    return tuple(
        any(is_alternative(alt, place) for alt in source.alternatives for place in verified)
        for source in sources
    )


def register_score(question: GoldQuestion, answer: Answer | None) -> tuple[int, int, int]:
    """The gold's agreements, how many of them the answer declares, and how many others it does.

    Agreements are compared by key, so -001 and -01 are one; all zero when
    the gold has no register source.
    """
    required = {
        agreement_key(number) or number
        for source in question.sources
        if isinstance(source, RegisterSource)
        for number in source.agreement_numbers
    }
    if not required:
        return 0, 0, 0
    declared = (
        {agreement_key(f.agreement_number) or f.agreement_number for f in answer.register_facts}
        if answer is not None
        else set()
    )
    return len(required), len(required & declared), len(declared - required)


def no_draft(run: QuestionRun) -> bool:
    """Whether the run ended without a draft, within the limit on model calls."""
    return run.answer is not None and run.answer.text == NO_DRAFT_TEXT and not run.answer.citations


def rule_judgement(run: QuestionRun) -> Judgement | None:
    """The verdict for an answer that needs no judge: a run that ended without a draft."""
    if no_draft(run):
        return Judgement(verdict="incorrect", missing=[], wrong=[], reason=NO_DRAFT_REASON)
    return None


def score(
    question: GoldQuestion,
    run: QuestionRun,
    judgement: Judgement | None,
    judged_by: JudgedBy | None,
    judge_usage: Mapping[str, TokenUse] | None = None,
    ask_judgement: AskJudgement | None = None,
) -> QuestionResult:
    """The question's result: the run, its sources and register rows scored, and the verdicts."""
    places = cited_places(run.answer, run.draft)
    required, found, extra = register_score(question, run.answer)
    return QuestionResult(
        id=question.id,
        category=question.category,
        answerable=question.answerable,
        difficulty=question.difficulty,
        question=question.question,
        gold_answer=question.answer,
        run=run,
        places=places,
        document_sources=len(question.document_sources),
        sources_found=sources_found(question.document_sources, places),
        register_required=required,
        register_found=found,
        register_extra=extra,
        judgement=judgement,
        judged_by=judged_by if judgement is not None else None,
        judge_usage=dict(judge_usage or {}),
        should_ask=question.should_ask,
        expected_options=question.options,
        ask_judgement=ask_judgement if question.should_ask and run.asked else None,
    )


def verdict_score(verdict: Verdict | None) -> float | None:
    """Correct 1, partly correct 0.5, incorrect 0; None for an answer not judged."""
    return VERDICT_SCORES[verdict] if verdict is not None else None


# --- Summaries (pure) -------------------------------------------------------------------------


@dataclass(frozen=True)
class Summary:
    """The numbers of a group of questions."""

    questions: int
    errors: int
    verdicts: Mapping[str, int]  # by verdict, and "unjudged"
    statuses: Mapping[str, int]  # by status, and "error" for a run without an answer
    unanswerable: int
    unanswerable_judged: int
    unanswerable_correct: int  # judged correct: the answer says it does not follow
    answerable: int
    answerable_no_answer: int  # the agent found no answer although the gold has one
    no_draft: int  # runs that ended without a draft
    citations: int
    verified_citations: int
    answers_with_citations: int
    answers_all_verified: int
    document_sources: int
    sources_found: int
    questions_with_sources: int
    questions_all_sources: int
    register_required: int
    register_found: int
    register_extra: int
    questions_with_register: int
    retried: int  # questions with at least one new attempt
    retries: int
    tool_calls: tuple[int, ...]  # per question
    asked: int  # questions where the agent asked the user
    seconds: tuple[float, ...]  # per question
    # The agent's and the reviewer's calls, low and high; None when a model has no price.
    cost: tuple[float, float] | None
    usage: Mapping[str, TokenUse]


def summarize(results: Sequence[QuestionResult]) -> Summary:
    """The summary of `results`."""
    verdicts = {verdict: 0 for verdict in VERDICTS} | {"unjudged": 0}
    statuses = {status: 0 for status in STATUSES} | {"error": 0}
    for result in results:
        verdicts[result.verdict or "unjudged"] += 1
        statuses[result.status or "error"] += 1
    answered = [r for r in results if r.citations]
    with_sources = [r for r in results if r.document_sources]
    with_register = [r for r in results if r.register_required]
    usage = total_use(r.run.usage for r in results)
    return Summary(
        questions=len(results),
        errors=sum(1 for r in results if r.run.error is not None),
        verdicts=verdicts,
        statuses=statuses,
        unanswerable=sum(1 for r in results if not r.answerable),
        unanswerable_judged=sum(1 for r in results if not r.answerable and r.verdict is not None),
        unanswerable_correct=sum(1 for r in results if not r.answerable and r.verdict == "correct"),
        answerable=sum(1 for r in results if r.answerable),
        answerable_no_answer=sum(
            1 for r in results if r.answerable and r.status == "no_answer" and not no_draft(r.run)
        ),
        no_draft=sum(1 for r in results if no_draft(r.run)),
        citations=sum(r.citations for r in results),
        verified_citations=sum(r.verified_citations for r in results),
        answers_with_citations=len(answered),
        answers_all_verified=sum(1 for r in answered if r.verified_citations == r.citations),
        document_sources=sum(r.document_sources for r in results),
        sources_found=sum(sum(r.sources_found) for r in results),
        questions_with_sources=len(with_sources),
        questions_all_sources=sum(1 for r in with_sources if all(r.sources_found)),
        register_required=sum(r.register_required for r in results),
        register_found=sum(r.register_found for r in results),
        register_extra=sum(r.register_extra for r in results),
        questions_with_register=len(with_register),
        retried=sum(1 for r in results if r.run.check_retries),
        retries=sum(r.run.check_retries for r in results),
        tool_calls=tuple(len(r.run.tools) for r in results),
        asked=sum(1 for r in results if r.run.asked),
        seconds=tuple(r.run.seconds for r in results),
        cost=cost_range(usage),
        usage=usage,
    )


@dataclass(frozen=True)
class AskSummary:
    """Whether the agent asked the user where the gold says it should, and where not.

    Only questions whose run shows whether it asked are counted (`QuestionResult.could_ask`).
    """

    should_ask: int  # questions where it should ask
    asked: int  # of those, where it asked
    separating: int  # of those, where the ask judge found its question separates the options
    unjudged: int  # of those, where the ask judge gave no verdict
    should_not_ask: int  # questions where it should answer without asking
    asked_unnecessarily: int  # of those, where it asked


class AskOutcome(Protocol):
    """What the asks are counted from: a `QuestionResult`, or a question of a JSON report."""

    @property
    def should_ask(self) -> bool | None: ...

    @property
    def asked(self) -> bool: ...

    @property
    def asked_right(self) -> bool | None: ...

    @property
    def could_ask(self) -> bool: ...


def summarize_asks(results: Sequence[AskOutcome]) -> AskSummary | None:
    """The `AskSummary` of `results`; None when no question that could ask says whether to.

    A question whose run saved nothing is left out of both counts, as `asked_right` leaves
    it unscored.
    """
    counted = [r for r in results if r.could_ask]
    should = [r for r in counted if r.should_ask is True]
    should_not = [r for r in counted if r.should_ask is False]
    if not should and not should_not:
        return None
    asked = [r for r in should if r.asked]
    return AskSummary(
        should_ask=len(should),
        asked=len(asked),
        separating=sum(1 for r in asked if r.asked_right),
        unjudged=sum(1 for r in asked if r.asked_right is None),
        should_not_ask=len(should_not),
        asked_unnecessarily=sum(1 for r in should_not if r.asked),
    )


@dataclass(frozen=True)
class ToolCount:
    calls: int
    questions: int  # that called the tool at least once


@dataclass(frozen=True)
class RuleCount:
    drafts: int  # drafts the rule sent back; a draft can be sent back by several rules
    questions: int
    problems: int  # lines of the feedback


@dataclass(frozen=True)
class PathSummary:
    """How the agent went about a group of questions (see the module's How)."""

    tools: Mapping[str, ToolCount]  # avtal-mcp's tools, most calls first
    questions_saved: int  # whose path was saved: the questions the counts below are of
    not_saved: int  # questions that never reached the graph
    model_calls: tuple[int, ...]  # per question whose path was saved
    reads: int  # read_section calls
    reads_from_references: int  # to a section an earlier result's references named
    reads_from_references_only: int  # of those, to a section no earlier search had returned
    reads_from_amendments: int  # to an amending section an earlier find_amendments named
    questions_from_references: int  # with at least one read from a reference
    questions_from_references_only: int  # with at least one such read no search had returned
    rejections: Mapping[CheckRule, RuleCount]  # by rule, in CHECK_RULES' order


def summarize_paths(results: Sequence[QuestionResult]) -> PathSummary:
    """The `PathSummary` of `results`."""
    calls: dict[str, int] = {}
    questions: dict[str, int] = {}
    for r in results:
        for tool in r.run.tools:
            calls[tool] = calls.get(tool, 0) + 1
        for tool in set(r.run.tools):
            questions[tool] = questions.get(tool, 0) + 1
    saved = [r.run for r in results if r.run.path_saved]
    reads = [step for run in saved for step in run.steps if step.name == READ_SECTION]
    by_rule = [rule_counts(run.rejections) for run in saved]
    problems: dict[CheckRule, int] = {}
    for run in saved:
        for rejection in run.rejections:
            for problem in rejection.problems:
                problems[problem.rule] = problems.get(problem.rule, 0) + 1
    return PathSummary(
        tools={
            tool: ToolCount(calls[tool], questions[tool])
            for tool in sorted(calls, key=lambda tool: (-calls[tool], tool))
        },
        questions_saved=len(saved),
        not_saved=len(results) - len(saved),
        model_calls=tuple(run.model_calls for run in saved if run.model_calls is not None),
        reads=len(reads),
        reads_from_references=sum(1 for step in reads if _from_reference(step)),
        reads_from_references_only=sum(1 for step in reads if _from_reference_only(step)),
        reads_from_amendments=sum(1 for step in reads if step.target_from == "amendment"),
        questions_from_references=sum(
            1 for run in saved if any(_from_reference(step) for step in run.steps)
        ),
        questions_from_references_only=sum(
            1 for run in saved if any(_from_reference_only(step) for step in run.steps)
        ),
        rejections={
            rule: RuleCount(
                drafts=sum(counts.get(rule, 0) for counts in by_rule),
                questions=sum(1 for counts in by_rule if rule in counts),
                problems=problems.get(rule, 0),
            )
            for rule in CHECK_RULES
            if any(rule in counts for counts in by_rule)
        },
    )


def _from_reference(step: Step) -> bool:
    """A read of a section an earlier result's references named."""
    return step.name == READ_SECTION and step.target_from == "reference"


def _from_reference_only(step: Step) -> bool:
    """A read of a section a reference named and no earlier search had returned."""
    return _from_reference(step) and not step.found_by_search


def by_category(results: Sequence[QuestionResult]) -> dict[str, list[QuestionResult]]:
    """The results by category, in the order the categories first appear."""
    groups: dict[str, list[QuestionResult]] = {}
    for result in results:
        groups.setdefault(result.category, []).append(result)
    return groups


def percentile(values: Sequence[float], share: float) -> float:
    """The nearest-rank percentile: the smallest value at or above `share` of the values."""
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[max(0, math.ceil(share * len(ordered)) - 1)]


def median(values: Sequence[float]) -> float:
    return statistics.median(values) if values else 0.0
