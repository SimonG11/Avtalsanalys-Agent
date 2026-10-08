"""Tests for evals.answer_scores: an answer scored against the gold, and the summaries.

The runs are made up: an `Answer` and its draft as the graph would leave
them, so the scores are tested without a model or a server.
"""

from collections.abc import Sequence
from dataclasses import replace
from datetime import date

import pytest

from avtalsagent.agent.middleware import NO_DRAFT_TEXT
from avtalsagent.agent.schemas import (
    Answer,
    AnswerStatus,
    Citation,
    DraftCitation,
    FinalAnswer,
    RegisterFact,
)
from evals.answer_run import QuestionRun, TokenUse
from evals.answer_scores import (
    NO_DRAFT_REASON,
    AskSummary,
    CitedPlace,
    QuestionResult,
    RuleCount,
    ToolCount,
    by_category,
    cited_places,
    is_alternative,
    median,
    percentile,
    register_score,
    rule_judgement,
    score,
    sources_found,
    summarize,
    summarize_asks,
    summarize_paths,
    verdict_score,
)
from evals.answer_steps import Problem, Rejection, Step, TargetSource
from evals.ask_judge import AskJudgement
from evals.gold import Alternative, DocumentSource, GoldQuestion, GoldScope, RegisterSource
from evals.judge import Judgement, Verdict

TERMS = "a" * 64
GUIDE = "b" * 64
COPY = "c" * 64


def alternative(sha256: str, number: str | None, position: int | None) -> Alternative:
    return Alternative(
        sha256=sha256,
        section_number=number,
        section_position=position,
        section_title="Vite",
        file_title="Allmänna villkor",
        page_title="IT-drift",
        pages=None,
        quote="vite",
    )


def gold(
    sources: Sequence[DocumentSource | RegisterSource] = (),
    *,
    id: str = "q01",
    category: str = "enkel uppslagning",
    answerable: bool = True,
) -> GoldQuestion:
    return GoldQuestion(
        id=id,
        category=category,
        area="IT-drift",
        scope=GoldScope(framework_area="IT-drift"),
        question="Hur stort är vitet?",
        answer="5 000 kronor per dag.",
        answerable=answerable,
        sources=tuple(sources),
        why_hard="",
        difficulty=1,
    )


def citation(id: int, sha256: str, number: str | None, verified: bool = True) -> Citation:
    return Citation(
        id=id,
        sha256=sha256,
        file_title="Allmänna villkor",
        page_title=None,
        section_number=number,
        section_title="Vite",
        page=3,
        quote="vite om 5 000 kronor",
        verified=verified,
    )


def draft(*places: tuple[int, str, int]) -> FinalAnswer:
    return FinalAnswer(
        answered=True,
        text="5 000 kronor [1].",
        citations=[
            DraftCitation(id=id, sha256=sha256, section_position=position, quote="vite om 5 000")
            for id, sha256, position in places
        ],
    )


def answer(
    *citations: Citation, status: AnswerStatus = "verified", text: str = "5 000 kronor [1]."
) -> Answer:
    return Answer(text=text, status=status, citations=list(citations))


def fact(number: str) -> RegisterFact:
    return RegisterFact(
        agreement_number=number,
        supplier_name="Exempel AB",
        org_number="556677-8899",
        sub_area="IT-drift Mindre",
        valid_from=date(2024, 1, 1),
        valid_to=date(2028, 1, 1),
        max_extension_to=None,
    )


def run(
    result: Answer | None = None,
    draft_: FinalAnswer | None = None,
    *,
    seconds: float = 10.0,
    retries: int = 0,
    tools: tuple[str, ...] = ("search_documents",),
    asked: tuple[str, ...] = (),
    usage: dict[str, TokenUse] | None = None,
    error: str | None = None,
) -> QuestionRun:
    return QuestionRun(
        answer=result,
        draft=draft_,
        tools=tools,
        tool_errors=0,
        asked=asked,
        check_retries=retries,
        refused_drafts=0,
        seconds=seconds,
        usage=usage or {},
        error=error,
    )


def judged(verdict: Verdict) -> Judgement:
    return Judgement(verdict=verdict, missing=[], wrong=[], reason="Skäl.")


# --- places and sources -----------------------------------------------------------------------


def test_each_citation_takes_its_position_from_the_draft_by_id() -> None:
    result = answer(citation(1, TERMS, "6.21.4"), citation(2, GUIDE, None, verified=False))

    places = cited_places(result, draft((2, GUIDE, 7), (1, TERMS, 41)))

    assert places == (
        CitedPlace(TERMS, 41, "6.21.4", True),
        CitedPlace(GUIDE, 7, None, False),
    )


def test_a_draft_citation_in_another_file_gives_no_position() -> None:
    places = cited_places(answer(citation(1, TERMS, "6.21.4")), draft((1, GUIDE, 41)))

    assert places == (CitedPlace(TERMS, None, "6.21.4", True),)
    assert cited_places(answer(citation(1, TERMS, "6.21.4")), None)[0].position is None
    assert cited_places(None, None) == ()


@pytest.mark.parametrize(
    ("alt", "place", "expected"),
    [
        (alternative(TERMS, "6.21.4", 41), CitedPlace(TERMS, 41, "6.21.4", True), True),
        # The position decides when both are known: the same number elsewhere in the file.
        (alternative(TERMS, "6.21.4", 41), CitedPlace(TERMS, 90, "6.21.4", True), False),
        (alternative(TERMS, "6.21.4", None), CitedPlace(TERMS, 90, "6.21.4", True), True),
        (alternative(TERMS, "6.21.4", 41), CitedPlace(TERMS, None, "6.21.4", True), True),
        (alternative(TERMS, None, 3), CitedPlace(TERMS, 3, None, True), True),
        (alternative(TERMS, None, 3), CitedPlace(TERMS, None, None, True), False),
        (alternative(TERMS, "6.21.4", None), CitedPlace(COPY, 41, "6.21.4", True), False),
    ],
)
def test_a_citation_is_an_alternative_in_the_same_file_and_place(
    alt: Alternative, place: CitedPlace, expected: bool
) -> None:
    assert is_alternative(alt, place) is expected


def test_a_source_is_found_by_any_alternative_with_a_checked_quote() -> None:
    sources = [
        DocumentSource((alternative(TERMS, "6.21.4", None), alternative(COPY, "6.21.4", None))),
        DocumentSource((alternative(GUIDE, None, 7),)),
    ]
    places = [CitedPlace(COPY, 12, "6.21.4", True), CitedPlace(GUIDE, 7, None, False)]

    assert sources_found(sources, places) == (True, False)
    assert sources_found([], places) == ()


# --- register ---------------------------------------------------------------------------------


def test_the_register_score_compares_agreements_by_key() -> None:
    question = gold([RegisterSource(("23.3-5890-2023-002", "23.3-5890-2023-003"), ("org",))])
    result = Answer(
        text="…",
        status="verified",
        citations=[],
        register_facts=[fact("23.3-5890-2023-02"), fact("23.3-5890-2023-007")],
    )

    assert register_score(question, result) == (2, 1, 1)
    assert register_score(question, None) == (2, 0, 0)
    assert register_score(gold(), result) == (0, 0, 0)


# --- verdicts and results ---------------------------------------------------------------------


def test_a_run_without_a_draft_is_incorrect_by_rule() -> None:
    no_draft = run(Answer(text=NO_DRAFT_TEXT, status="no_answer", citations=[]))

    judgement = rule_judgement(no_draft)

    assert judgement == Judgement(verdict="incorrect", missing=[], wrong=[], reason=NO_DRAFT_REASON)
    assert rule_judgement(run(answer(status="no_answer", text="Framgår inte."))) is None
    assert rule_judgement(run(None, error="avtal-mcp: ConnectError")) is None


def test_a_result_scores_the_run_against_the_question() -> None:
    question = gold(
        [
            DocumentSource((alternative(TERMS, "6.21.4", None),)),
            DocumentSource((alternative(GUIDE, None, 7),)),
            RegisterSource(("23.3-5890-2023-002",), ("org",)),
        ]
    )
    result_answer = Answer(
        text="5 000 kronor [1].",
        status="verified",
        citations=[citation(1, TERMS, "6.21.4")],
        register_facts=[fact("23.3-5890-2023-002")],
    )
    usage = {"gpt-6.1-sol": TokenUse(calls=1, input_tokens=1_000_000)}

    result = score(question, run(result_answer, usage=usage), judged("correct"), "judge")

    assert (result.id, result.status, result.verdict, result.judged_by) == (
        "q01",
        "verified",
        "correct",
        "judge",
    )
    assert (result.citations, result.verified_citations) == (1, 1)
    assert (result.document_sources, result.sources_found) == (2, (True, False))
    assert (result.register_required, result.register_found, result.register_extra) == (1, 1, 0)
    assert result.cost == pytest.approx((2.0, 2.0))
    unjudged = score(question, run(None, error="fel"), None, "judge")
    assert (unjudged.status, unjudged.verdict, unjudged.judged_by) == (None, None, None)


# --- summaries --------------------------------------------------------------------------------


def results() -> list[QuestionResult]:
    found = gold([DocumentSource((alternative(TERMS, "6.21.4", None),))], id="q01")
    missed = gold([DocumentSource((alternative(GUIDE, None, 7),))], id="q02")
    register = gold(
        [RegisterSource(("23.3-5890-2023-002",), ("org",))], id="q10", category="registerfråga"
    )
    unanswerable = gold(id="q27", category="fråga utan svar i avtalen", answerable=False)
    return [
        score(
            found, run(answer(citation(1, TERMS, "6.21.4")), seconds=10), judged("correct"), "judge"
        ),
        score(
            missed,
            run(
                answer(citation(1, TERMS, "6.21.4", verified=False), status="with_reservation"),
                seconds=30,
                retries=2,
                asked=("Vilket avtal?",),
            ),
            judged("partly_correct"),
            "judge",
        ),
        score(
            register,
            run(answer(status="no_answer", text="Framgår inte."), seconds=20, tools=()),
            judged("incorrect"),
            "judge",
        ),
        score(
            unanswerable, run(answer(status="no_answer"), seconds=40), judged("correct"), "judge"
        ),
        score(gold(id="q30"), run(None, seconds=600, error="tidsgräns"), None, None),
    ]


def test_the_summary_counts_verdicts_statuses_sources_and_register_rows() -> None:
    summary = summarize(results())

    assert summary.questions == 5
    assert summary.errors == 1
    assert summary.verdicts == {"correct": 2, "partly_correct": 1, "incorrect": 1, "unjudged": 1}
    assert summary.statuses == {"verified": 1, "with_reservation": 1, "no_answer": 2, "error": 1}
    assert (summary.unanswerable, summary.unanswerable_judged, summary.unanswerable_correct) == (
        1,
        1,
        1,
    )
    assert (summary.answerable, summary.answerable_no_answer, summary.no_draft) == (4, 1, 0)
    assert (summary.citations, summary.verified_citations) == (2, 1)
    assert (summary.answers_with_citations, summary.answers_all_verified) == (2, 1)
    assert (summary.document_sources, summary.sources_found) == (2, 1)
    assert (summary.questions_with_sources, summary.questions_all_sources) == (2, 1)
    assert (summary.register_required, summary.register_found, summary.register_extra) == (1, 0, 0)
    assert (summary.retried, summary.retries, summary.asked) == (1, 2, 1)
    assert summary.tool_calls == (1, 1, 0, 1, 1)
    assert summary.seconds == (10, 30, 20, 40, 600)
    assert summary.cost == (0.0, 0.0)


def test_a_run_without_a_draft_is_not_counted_as_finding_no_answer() -> None:
    no_draft = run(Answer(text=NO_DRAFT_TEXT, status="no_answer", citations=[]))
    unanswerable = gold(id="q28", category="fråga utan svar i avtalen", answerable=False)
    group = [
        score(gold(id="q05"), no_draft, rule_judgement(no_draft), "rule"),
        score(unanswerable, run(answer(status="no_answer")), None, None),
    ]

    summary = summarize(group)

    assert (summary.answerable_no_answer, summary.no_draft) == (0, 1)
    assert (summary.unanswerable, summary.unanswerable_judged) == (1, 0)


def test_an_empty_summary_has_zeros() -> None:
    summary = summarize([])

    assert summary.questions == 0
    assert summary.cost == (0.0, 0.0)
    assert median(summary.seconds) == 0.0
    assert percentile(summary.seconds, 0.9) == 0.0


def read(target_from: TargetSource | None = None, found_by_search: bool = False) -> Step:
    return Step(
        "read_section",
        {"sha256": TERMS, "section_position": 1},
        target_from=target_from,
        found_by_search=found_by_search,
    )


def test_the_paths_count_tools_model_calls_reads_and_rejections_by_rule() -> None:
    cited = Rejection(
        (Problem("citations", "a"), Problem("citations", "b"), Problem("register_facts", "c"))
    )
    reviewed = Rejection((Problem("review", "d"),))
    runs = [
        replace(
            run(tools=("search_documents", "read_section", "read_section"), retries=2),
            steps=(
                Step("search_documents", {"query": "vite"}),
                read(found_by_search=True),
                read("reference", found_by_search=True),
            ),
            model_calls=6,
            rejections=(cited, reviewed),
        ),
        replace(
            run(tools=("read_section", "resolve_reference", "read_section")),
            steps=(
                read("amendment"),
                Step("resolve_reference", {"sha256": TERMS, "section_position": 1}),
                read("reference"),
            ),
            model_calls=16,
        ),
        replace(run(tools=()), model_calls=0),  # reached the graph, but no call finished
        run(None, tools=(), error="avtal-mcp: ConnectError"),  # never reached the graph
    ]

    paths = summarize_paths([score(gold(id=f"q0{n}"), r, None, None) for n, r in enumerate(runs)])

    assert paths.tools == {
        "read_section": ToolCount(calls=4, questions=2),
        "resolve_reference": ToolCount(calls=1, questions=1),
        "search_documents": ToolCount(calls=1, questions=1),
    }
    assert list(paths.tools) == ["read_section", "resolve_reference", "search_documents"]
    # The question that never reached the graph is not saved; the one without calls counts.
    assert (paths.questions_saved, paths.not_saved) == (3, 1)
    assert paths.model_calls == (6, 16, 0)
    assert (paths.reads, paths.reads_from_references, paths.reads_from_amendments) == (4, 2, 1)
    # Of the two reads from a reference, one went to a section a search had returned.
    assert (paths.reads_from_references_only, paths.questions_from_references_only) == (1, 1)
    assert paths.questions_from_references == 2
    assert paths.rejections == {
        "citations": RuleCount(drafts=1, questions=1, problems=2),
        "register_facts": RuleCount(drafts=1, questions=1, problems=1),
        "review": RuleCount(drafts=1, questions=1, problems=1),
    }


def test_the_paths_of_no_questions_are_empty() -> None:
    paths = summarize_paths([])

    assert (paths.tools, paths.model_calls, paths.reads, paths.rejections) == ({}, (), 0, {})
    assert (paths.questions_saved, paths.not_saved) == (0, 0)


def test_the_results_are_grouped_by_category_in_order() -> None:
    groups = by_category(results())

    assert list(groups) == ["enkel uppslagning", "registerfråga", "fråga utan svar i avtalen"]
    assert [r.id for r in groups["enkel uppslagning"]] == ["q01", "q02", "q30"]


def test_the_percentile_is_nearest_rank() -> None:
    values = [float(n) for n in range(1, 11)]

    assert percentile(values, 0.9) == 9.0
    assert percentile(values, 0.5) == 5.0
    assert percentile([7.0], 0.9) == 7.0
    assert median([3.0, 1.0, 2.0]) == 2.0


# --- questions to the user ---------------------------------------------------------------------

SEPARATES = AskJudgement(separates=True, reason="Delområdena går att välja.")
MIXES = AskJudgement(separates=False, reason="Delområde 1 och 3 slås ihop.")
OPTIONS = ("Delområde 1", "Delområde 3")


def asking(id: str, should_ask: bool | None) -> GoldQuestion:
    question = replace(gold(id=id), should_ask=should_ask)
    if should_ask is None:
        return question
    return replace(question, options=OPTIONS, clarification="Delområde 3.")


def asked_run(*questions: str) -> QuestionRun:
    """A run that reached the graph (its path was read), and asked `questions`."""
    options = tuple(OPTIONS for _ in questions)
    return replace(run(answer()), asked=questions, asked_options=options, model_calls=2)


def test_an_ask_is_right_only_when_it_should_ask_asked_and_separates() -> None:
    right = score(asking("a01", True), asked_run("Vilket?"), None, None, ask_judgement=SEPARATES)
    mixed = score(asking("a02", True), asked_run("Vilket?"), None, None, ask_judgement=MIXES)
    unjudged = score(asking("a03", True), asked_run("Vilket?"), None, None)
    silent = score(asking("a04", True), asked_run(), None, None, ask_judgement=SEPARATES)

    assert (right.asked, right.asked_right, right.unnecessary_ask) == (True, True, False)
    assert (mixed.asked_right, unjudged.asked_right) == (False, None)
    assert (silent.asked, silent.asked_right) == (False, False)
    assert silent.ask_judgement is None  # a judgement of no question is not kept
    assert right.expected_options == OPTIONS and right.should_ask is True


def test_not_asking_is_right_where_it_should_not_and_an_ask_there_is_unnecessary() -> None:
    quiet = score(asking("a05", False), asked_run(), None, None)
    needless = score(asking("a06", False), asked_run("Större eller Mindre?"), None, None)
    plain = score(asking("q01", None), asked_run("Vilket?"), None, None)
    # A run that never reached the graph had no chance to ask, or not to.
    broken = run(None, error="avtal-mcp: ConnectError")
    lost = [
        score(asking(id, should), broken, None, None)
        for id, should in (("a07", True), ("a08", False))
    ]

    assert (quiet.asked_right, quiet.unnecessary_ask) == (True, False)
    assert [result.asked_right for result in lost] == [None, None]
    assert (needless.asked_right, needless.unnecessary_ask) == (False, True)
    assert (plain.asked_right, plain.unnecessary_ask, plain.should_ask) == (None, None, None)


def test_the_asks_summary_counts_the_asks_and_is_none_without_ask_questions() -> None:
    results = [
        score(asking("a01", True), asked_run("Vilket?"), None, None, ask_judgement=SEPARATES),
        score(asking("a02", True), asked_run("Vilket?"), None, None, ask_judgement=MIXES),
        score(asking("a03", True), asked_run("Vilket?"), None, None),
        score(asking("a04", True), asked_run(), None, None),
        score(asking("a05", False), asked_run("Båda?"), None, None),
        score(asking("a06", False), asked_run(), None, None),
        score(asking("q01", None), asked_run("Vilket?"), None, None),
    ]

    assert summarize_asks(results) == AskSummary(
        should_ask=4,
        asked=3,
        separating=1,
        unjudged=1,
        should_not_ask=2,
        asked_unnecessarily=1,
    )
    assert summarize_asks(results[-1:]) is None


def test_a_run_that_never_reached_the_graph_and_did_not_ask_is_in_neither_count() -> None:
    # As the agent's a04 on 2026-10-08: a transport error before the graph, and no ask.
    broken = run(None, error="avtal-mcp: ReadError")
    results = [
        score(asking("a01", True), asked_run("Vilket?"), None, None, ask_judgement=SEPARATES),
        score(asking("a02", True), broken, None, None),
        score(asking("a05", False), asked_run("Båda?"), None, None),
        score(asking("a06", False), asked_run(), None, None),
        score(asking("a07", False), broken, None, None),
    ]

    assert [result.could_ask for result in results] == [True, False, True, True, False]
    assert summarize_asks(results) == AskSummary(
        should_ask=1,
        asked=1,
        separating=1,
        unjudged=0,
        should_not_ask=2,
        asked_unnecessarily=1,
    )
    # With only such runs there is nothing to count.
    assert summarize_asks([results[1], results[4]]) is None


def test_a_run_that_asked_before_it_failed_is_counted() -> None:
    # Its path was not saved, but it did ask: the ask is there to judge.
    cut = replace(
        run(None, error="avtal-mcp: ReadError"), asked=("Vilket?",), asked_options=(OPTIONS,)
    )
    results = [
        score(asking("a01", True), cut, None, None),
        score(asking("a02", False), cut, None, None),
    ]

    assert [(result.could_ask, result.asked_right) for result in results] == [
        (True, None),
        (True, False),
    ]
    assert summarize_asks(results) == AskSummary(
        should_ask=1,
        asked=1,
        separating=0,
        unjudged=1,
        should_not_ask=1,
        asked_unnecessarily=1,
    )


def test_a_run_that_never_asks_asks_in_none_of_the_questions_that_should_ask() -> None:
    results = [score(asking(f"a0{n}", True), asked_run(), None, None) for n in range(1, 5)]

    asks = summarize_asks(results)

    assert asks is not None and (asks.should_ask, asks.asked, asks.separating) == (4, 0, 0)
    assert (asks.should_not_ask, asks.asked_unnecessarily) == (0, 0)


def test_a_verdict_scores_one_a_half_or_none() -> None:
    assert [verdict_score(v) for v in ("correct", "partly_correct", "incorrect", None)] == [
        1.0,
        0.5,
        0.0,
        None,
    ]
