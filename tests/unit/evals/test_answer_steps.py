"""Tests for evals.answer_steps: the agent's tool calls and the check's rejections.

The messages are written as the graph leaves them, with avtal-mcp's results
as the MCP adapter gives them (the JSON text and the structured content as
artifact), built from avtal-mcp's own result models. Each rule's problems
come from the rule itself, so a rule that words its problems anew fails
here instead of being counted under another rule.
"""

import json
from datetime import date
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from pydantic import BaseModel

from avtalsagent.agent.amendments import AmendmentInfo
from avtalsagent.agent.graph import ANSWER_SUBMITTED
from avtalsagent.agent.middleware import FIX_INSTRUCTION
from avtalsagent.agent.register_reader import RegisterEntry
from avtalsagent.agent.schemas import DraftCitation, FinalAnswer
from avtalsagent.agent.sections import CitedSection
from avtalsagent.mcp_server.references import SectionReference, TargetRef
from avtalsagent.mcp_server.results import SectionRef
from avtalsagent.mcp_server.tools.find_amendments import Amendment, AmendmentResult
from avtalsagent.mcp_server.tools.read_section import Section
from avtalsagent.mcp_server.tools.resolve_reference import ReferenceResult
from avtalsagent.validation.citations import check_citations
from avtalsagent.validation.latest_wording import check_latest_wording
from avtalsagent.validation.register_facts import check_register_facts
from avtalsagent.validation.review import ClaimReview, ReviewVerdict, review_problems
from evals.answer_steps import (
    Problem,
    Rejection,
    Step,
    feedback_problems,
    problem_rule,
    read_rejections,
    read_steps,
    rule_counts,
    structured_result,
)

TERMS = "a1" * 32  # Allmänna villkor
PRICES = "b2" * 32  # a whole file a reference points to
LOG = "c3" * 32  # a questions log that changes a section of TERMS
TODAY = date(2026, 10, 7)


def section_ref(sha256: str, position: int, number: str | None, title: str) -> SectionRef:
    return SectionRef(
        sha256=sha256,
        file_title="Allmänna villkor",
        document_type="general_terms",
        page_titles=["IT-drift Mindre"],
        section_position=position,
        section_number=number,
        section_title=title,
        page_start=14,
        page_end=14,
    )


def target(sha256: str, position: int | None, number: str | None) -> TargetRef:
    return TargetRef(
        sha256=sha256,
        file_title="Bilaga Priser" if sha256 == PRICES else "Allmänna villkor",
        document_type="price_list" if sha256 == PRICES else "general_terms",
        page_titles=["IT-drift Mindre"],
        section_position=position,
        section_number=number,
        section_title=None if position is None else "Hävning",
        page_start=None if position is None else 13,
        page_end=None if position is None else 13,
    )


# 6.21.9 refers to 6.21.4 (position 37) and to the whole price list.
UPPSAGNING = Section(
    **section_ref(TERMS, 42, "6.21.9", "Uppsägning").model_dump(),
    path=["6 Allmänna villkor", "6.21.9 Uppsägning"],
    text="Leverantören får säga upp Kontraktet enligt punkt 6.21.4 och bilaga Priser.",
    references=[
        SectionReference(
            raw="punkt 6.21.4",
            kind="section_number",
            status="resolved",
            targets=[target(TERMS, 37, "6.21.4")],
            held_back_targets=0,
        ),
        SectionReference(
            raw="bilaga Priser",
            kind="title",
            status="resolved",
            targets=[target(PRICES, None, None)],
            held_back_targets=0,
        ),
    ],
    held_back_targets=0,
)


def call(name: str, args: dict[str, Any], call_id: str) -> dict[str, Any]:
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


def calls(*tool_calls: dict[str, Any]) -> AIMessage:
    return AIMessage(content="", tool_calls=list(tool_calls))


def mcp_result(name: str, call_id: str, result: BaseModel) -> ToolMessage:
    """A tool result as langchain-mcp-adapters gives it: JSON text, the structure as artifact."""
    data = result.model_dump(mode="json")
    return ToolMessage(
        content=[{"type": "text", "text": json.dumps(data, ensure_ascii=False)}],
        tool_call_id=call_id,
        name=name,
        artifact={"structured_content": data},
    )


def plain_result(name: str, call_id: str, text: str, status: str = "success") -> ToolMessage:
    return ToolMessage(content=text, tool_call_id=call_id, name=name, status=status)


# --- the calls --------------------------------------------------------------------------------


def test_every_call_is_kept_in_order_with_its_arguments_and_outcome() -> None:
    messages: list[BaseMessage] = [
        HumanMessage("Hur sägs avtalet upp?"),
        calls(call("ask_user", {"question": "Vilket avtal?", "options": ["A", "B"]}, "c1")),
        plain_result("ask_user", "c1", "A"),
        calls(
            call("search_documents", {"query": "uppsägning", "framework_area": "IT-drift"}, "c2"),
            call("read_section", {"sha256": TERMS, "section_number": "9.9"}, "c3"),
        ),
        plain_result("search_documents", "c2", '{"hits": []}'),
        plain_result("read_section", "c3", "Det finns inget avsnitt med nummer 9.9.", "error"),
        calls(call("FinalAnswer", {"answered": True, "text": "Ett."}, "c4")),
        plain_result(
            "FinalAnswer", "c4", "Kontrollen underkände svaret (försök 1 av 3):\n- x", "error"
        ),
        calls(call("FinalAnswer", {"text": "Saknar answered."}, "c5")),
        plain_result("FinalAnswer", "c5", "Error: answered saknas"),
        calls(call("FinalAnswer", {"answered": True, "text": "Två."}, "c6")),
        plain_result("FinalAnswer", "c6", ANSWER_SUBMITTED),
        calls(call("get_outline", {"sha256": TERMS}, "c7")),  # the run ended before its result
    ]

    steps = read_steps(messages)

    assert steps == (
        Step("ask_user", {"question": "Vilket avtal?", "options": ["A", "B"]}),
        Step("search_documents", {"query": "uppsägning", "framework_area": "IT-drift"}),
        Step("read_section", {"sha256": TERMS, "section_number": "9.9"}, error=True),
        Step("FinalAnswer", {"answered": True, "text": "Ett."}, draft="sent_back"),
        Step("FinalAnswer", {"text": "Saknar answered."}, draft="refused"),
        Step("FinalAnswer", {"answered": True, "text": "Två."}, draft="submitted"),
        Step("get_outline", {"sha256": TERMS}),
    )


def test_a_read_of_a_section_an_earlier_result_refers_to_is_from_a_reference() -> None:
    messages: list[BaseMessage] = [
        calls(call("read_section", {"sha256": TERMS, "section_number": "6.21.9"}, "c1")),
        mcp_result("read_section", "c1", UPPSAGNING),
        calls(
            call("read_section", {"sha256": TERMS, "section_position": 37}, "c2"),
            call("read_section", {"sha256": TERMS, "section_number": " 6.21.4. "}, "c3"),
            call("read_section", {"sha256": TERMS, "section_position": "37"}, "c4"),
            # A whole file is no section target, and another section of the file is none either.
            call("read_section", {"sha256": PRICES, "section_position": 0}, "c5"),
            call("read_section", {"sha256": TERMS, "section_position": 38}, "c6"),
        ),
    ]

    steps = read_steps(messages)

    assert [step.target_from for step in steps] == [
        None,
        "reference",
        "reference",
        "reference",
        None,
        None,
    ]


def test_only_a_result_before_the_call_counts_and_resolve_reference_lists_targets_too() -> None:
    resolved = ReferenceResult(
        section=section_ref(TERMS, 42, "6.21.9", "Uppsägning"),
        references=UPPSAGNING.references,
        held_back_targets=0,
    )
    to_target = {"sha256": TERMS, "section_position": 37}
    messages: list[BaseMessage] = [
        # Asked at once: the reference's result came after the read was decided.
        calls(
            call("resolve_reference", {"sha256": TERMS, "section_number": "6.21.9"}, "c1"),
            call("read_section", to_target, "c2"),
        ),
        mcp_result("resolve_reference", "c1", resolved),
        plain_result("read_section", "c2", "{}"),
        calls(call("read_section", to_target, "c3")),
    ]

    assert [step.target_from for step in read_steps(messages)] == [None, None, "reference"]


def test_a_read_of_an_amending_section_find_amendments_named_is_from_an_amendment() -> None:
    amended = target(TERMS, 42, "6.21.9")
    found = AmendmentResult(
        target=amended,
        amendments=[
            Amendment(
                amending=section_ref(LOG, 6, "9", "Publik fråga"),
                amended=amended,
                raw="punkt 6.21.9",
                status="resolved",
                dated=date(2024, 2, 20),
                excerpt="Rättelse. Texten som gäller är följande för punkt 6.21.9: …",
            )
        ],
        held_back=0,
    )
    messages: list[BaseMessage] = [
        calls(call("find_amendments", {"sha256": TERMS, "section_position": 42}, "c1")),
        mcp_result("find_amendments", "c1", found),
        calls(call("read_section", {"sha256": LOG, "section_position": 6}, "c2")),
    ]

    assert read_steps(messages)[1].target_from == "amendment"


def test_a_result_without_an_artifact_is_read_from_its_json_text() -> None:
    text = json.dumps(UPPSAGNING.model_dump(mode="json"))
    messages: list[BaseMessage] = [
        calls(call("read_section", {"sha256": TERMS, "section_position": 42}, "c1")),
        plain_result("read_section", "c1", text),
        calls(call("read_section", {"sha256": TERMS, "section_position": 37}, "c2")),
    ]

    assert read_steps(messages)[1].target_from == "reference"
    assert structured_result(plain_result("read_section", "c3", "inte JSON")) is None
    assert structured_result(plain_result("read_section", "c4", "[1, 2]")) is None


# --- the rules' problems ----------------------------------------------------------------------

CITED = CitedSection(
    sha256=TERMS,
    section_position=42,
    section_number="6.21.9",
    section_title="Uppsägning",
    file_title="Allmänna villkor",
    page_titles=["IT-drift Mindre"],
    page_start=14,
    text="Kontraktet kan sägas upp av Kunden med en uppsägningstid om tre (3) månader.",
)
GOOD_QUOTE = "uppsägningstid om tre (3) månader"


def source(id: int, position: int, quote: str) -> DraftCitation:
    return DraftCitation(id=id, sha256=TERMS, section_position=position, quote=quote)


def test_every_problem_of_the_citation_check_is_counted_under_citations() -> None:
    # A marker without a source, a source not used, a shared id, a gap, and each kind of
    # failed source: a quote not in the section, one too short, a section not there.
    draft = FinalAnswer(
        answered=True,
        text="Tre månader [1] [5].",
        citations=[
            source(1, 42, "uppsägningstid på tre månader"),
            source(1, 42, "tre"),
            source(3, 99, GOOD_QUOTE),
        ],
    )

    problems = check_citations(draft, {(TERMS, 42): CITED}).problems

    assert len(problems) == 7
    assert {problem_rule(problem) for problem in problems} == {"citations"}


def entry(number: str, supplier: str, org_number: str) -> RegisterEntry:
    return RegisterEntry(
        agreement_number=number,
        procurement_number=number.rpartition("-")[0],
        supplier_name=supplier,
        org_number=org_number,
        former_names=[],
        framework_area="IT-drift",
        sub_area="IT-drift / IT-drift Mindre, upp till 200 anställda",
        valid_from=date(2024, 11, 14),
        valid_to=date(2028, 11, 13),
        max_extension_to=None,
    )


def test_every_problem_of_the_register_rule_is_counted_under_register_facts() -> None:
    advance = entry("23.3-5890-2023-002", "Nordlo Advance AB", "556486-1689")
    improve = entry("23.3-10639-2023-007", "Nordlo Improve AB", "556271-9129")
    telia = entry("23.3-8027-2021-004", "Telia Cygate AB", "556549-8952")
    text = (
        "Nordlo Advance AB har avtal 23.3-5890-2023-002 med organisationsnummer 556271-9129 "
        "till 2028-11-14.\n"
        "Organisationsnumret 556486-1680 hör till Nordlo Improve AB.\n"
        "Avtalet 23.3-4444-2023-001 och upphandlingen 23.3-7777-2022 nämns också, liksom "
        "2025-02-30 och numret 23.3-5890-2023-0021.\n"
        "Uppsägning senast 2028-11-13 minus 3 månader = 2028-08-14."
    )
    declared = [advance, improve, telia]
    draft = FinalAnswer(
        answered=True,
        text=text,
        register_facts=[*(e.agreement_number for e in declared), "23.3-0000-2020-001", "x"],
    )

    problems = check_register_facts(
        draft, {e.agreement_number: [e] for e in declared}, [], [], TODAY
    ).problems

    assert {problem.split(" ", 1)[0] for problem in problems} == {
        "Avtalsnumret",
        "Avtalet",
        "Numret",
        "Organisationsnumret",
        "Datumet",
        "Uträkningen",
    }
    assert {problem_rule(problem) for problem in problems} == {"register_facts"}


def test_the_latest_wording_rules_problem_is_not_taken_for_a_citations() -> None:
    draft = FinalAnswer(
        answered=True, text="Tre månader [1].", citations=[source(1, 42, GOOD_QUOTE)]
    )
    change = AmendmentInfo(
        sha256=LOG,
        section_position=6,
        file_title="Frågor och svar (med parentes)",
        section_number="9",
        section_title="Publik fråga",
        amended_position=42,
        status="resolved",
        dated=date(2024, 2, 20),
    )
    citations = check_citations(draft, {(TERMS, 42): CITED}).citations

    (problem,) = check_latest_wording(draft, citations, {(TERMS, 42): [change]}).problems

    assert problem.startswith("Källa [1] (")
    assert problem_rule(problem) == "latest_wording"


def test_every_problem_of_the_review_is_counted_under_review() -> None:
    verdict = ReviewVerdict(
        claims=[
            ClaimReview(
                claim="Källa [1] säger sex månader.",
                citation_ids=[1],
                support="unsupported",
                reason="Källan säger tre.",
            ),
            ClaimReview(claim="", citation_ids=[], support="partly", reason=""),
            ClaimReview(claim="Tre månader.", citation_ids=[1], support="supported", reason=""),
        ],
        missing=["Att uppsägningen ska vara skriftlig."],
    )

    problems = review_problems(verdict)

    assert len(problems) == 3
    assert [problem_rule(problem) for problem in problems] == ["review"] * 3


def test_a_problem_no_rule_writes_is_unknown() -> None:
    assert problem_rule("Något annat gick fel.") == "unknown"


# --- the feedback -----------------------------------------------------------------------------


def feedback(*problems: str) -> str:
    """The check's feedback as AnswerCheck writes it."""
    listed = "\n".join(f"- {problem}" for problem in problems)
    return f"Kontrollen underkände svaret (försök 1 av 3):\n{listed}\n{FIX_INSTRUCTION}"


def test_the_feedback_gives_each_problem_with_its_rule() -> None:
    date_problem = "Datumet 31\nmars 2025 står inte i registret."  # a value over a line break

    problems = feedback_problems(
        feedback("Källa [1]: citatet är för kort.", date_problem, "Saknas: Formen.")
    )

    assert problems == (
        Problem("citations", "Källa [1]: citatet är för kort."),
        Problem("register_facts", date_problem),
        Problem("review", "Saknas: Formen."),
    )


def test_a_feedback_of_another_form_is_kept_whole_as_unknown() -> None:
    assert feedback_problems("Verktyget avbröts.\n") == (Problem("unknown", "Verktyget avbröts."),)


def test_the_rejections_are_the_drafts_sent_back_in_order() -> None:
    messages: list[BaseMessage] = [
        calls(call("FinalAnswer", {}, "c1")),
        plain_result("FinalAnswer", "c1", feedback("Källa [1]: citatet är för kort."), "error"),
        calls(call("FinalAnswer", {}, "c2")),
        plain_result("FinalAnswer", "c2", feedback("”Sex månader” [1]: stöds inte."), "error"),
        calls(call("search_documents", {"query": "x"}, "c3")),
        plain_result("search_documents", "c3", "Sökningen är inte tillgänglig.", "error"),
        calls(call("FinalAnswer", {}, "c4")),
        plain_result("FinalAnswer", "c4", ANSWER_SUBMITTED),
    ]

    rejections = read_rejections(messages)

    assert [rejection.rules for rejection in rejections] == [("citations",), ("review",)]


def test_a_draft_is_counted_once_per_rule_that_sent_it_back() -> None:
    both = Rejection(
        (
            Problem("citations", "a"),
            Problem("citations", "b"),
            Problem("register_facts", "c"),
        )
    )
    review = Rejection((Problem("review", "d"),))

    assert both.rules == ("citations", "register_facts")
    assert rule_counts([both, review, both]) == {"citations": 2, "register_facts": 2, "review": 1}
    assert rule_counts([]) == {}
