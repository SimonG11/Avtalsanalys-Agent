"""Tests for evals.workflow_baseline: the fixed workflow, its prompt and its graph.

The graph runs offline through `answer_run.run_question`, as the evaluation
runs it: a scripted model writes the plan and the drafts, LangChain tools
stand in for avtal-mcp's (their results carry the structure the adapter
gives), and the test doubles of tests/unit/agent are the check's readers and
the reviewer. No test needs a network, an API key or a database.
"""

import json
import uuid
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool, ToolException, tool
from langgraph.checkpoint.memory import InMemorySaver

from avtalsagent.agent.checkpointer import serializer
from avtalsagent.agent.graph import AvtalAgent
from avtalsagent.agent.mcp_tools import McpTools
from avtalsagent.agent.prompts import SYSTEM_PROMPT
from avtalsagent.agent.sections import CitedSection
from avtalsagent.config import Settings
from evals.answer_run import QuestionRun, TokenUse, run_question
from evals.answer_steps import Step
from evals.gold import load_gold
from evals.workflow_baseline import (
    FIXED_STEPS,
    MAX_AMENDING,
    MAX_ANSWERS,
    MAX_SECTIONS,
    PILOT_AREAS,
    QUESTIONS_AND_ANSWERS,
    _hits,
    build_workflow,
    shared_rules,
    workflow_prompt,
    workflow_template,
)
from tests.unit.agent.scripted_model import (
    DictAmendments,
    DictReader,
    ListRegister,
    ScriptedModel,
    ScriptedReviewer,
    final_answer,
    tool_call,
)

TODAY = date(2026, 10, 7)
QUESTION = "Vilken uppsägningstid gäller i IT-drift Mindre?"
SHA = {name: name * 64 for name in "abcdefg"}
QA = {n: f"{n}" * 64 for n in "1234"}
# The search's hits: a copy of the first hit, then five more files; the first five distinct
# sections are read.
HITS = [("a", 41, "6.21.9"), ("a", 41, "6.21.9"), ("b", 10, "2.1"), ("c", 5, None)]
HITS += [("d", 7, "3.3"), ("e", 8, "4.4"), ("f", 9, "5.5")]
AMENDING = ("g", 3, "7")  # changes a's 6.21.9; b's 2.1 is changed by a, which is read already
TEXT = "Kontraktet kan sägas upp av Kunden med en uppsägningstid om tre (3) månader."
SECTION = CitedSection(
    sha256=SHA["a"],
    section_position=41,
    section_number="6.21.9",
    section_title="Uppsägning",
    file_title="Allmänna villkor",
    page_titles=["IT-drift Mindre"],
    page_start=14,
    text=TEXT,
)
GOOD = {"id": 1, "sha256": SHA["a"], "section_position": 41, "quote": "uppsägningstid om tre (3)"}
BAD = {"id": 1, "sha256": SHA["a"], "section_position": 41, "quote": "uppsägningstid på tre år"}
PLAN = {
    "framework_area": "IT-drift",
    "sub_area": "IT-drift Mindre",
    "agreement_number": None,
    "supplier": None,
    "search_query": "uppsägningstid kontrakt",
    "qa_query": "uppsägning av kontrakt",
}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class NamedModel(ScriptedModel):
    """A scripted model with a name, which LangChain reports as `ls_model_name`."""

    model: str = "the-agent-model"


def structured(data: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """A result as langchain-mcp-adapters gives an avtal-mcp result: JSON, the structure beside."""
    return json.dumps(data, ensure_ascii=False), {"structured_content": data}


def hit(file: str, position: int, number: str | None) -> dict[str, Any]:
    return {"sha256": SHA.get(file, file), "section_position": position, "section_number": number}


@tool(response_format="content_and_artifact")
def search_register(
    framework_area: str | None = None,
    sub_area: str | None = None,
    agreement_number: str | None = None,
    supplier: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Sök i registret."""
    return structured({"rows": [], "total": 0})


@tool(response_format="content_and_artifact")
def search_documents(
    query: str,
    framework_area: str | None = None,
    agreement_number: str | None = None,
    document_type: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Sök i dokumenten."""
    if document_type == QUESTIONS_AND_ANSWERS:  # g first: an amendment read already
        hits = [hit(*AMENDING)] + [hit(QA[n], 1, None) for n in "1234"]
    else:
        hits = [hit(*place) for place in HITS]
    return structured({"hits": hits})


@tool(response_format="content_and_artifact")
def read_section(
    sha256: str, section_number: str | None = None, section_position: int | None = None
) -> tuple[str, dict[str, Any]]:
    """Läs ett avsnitt."""
    return structured(
        {"sha256": sha256, "section_position": section_position, "text": TEXT, "references": []}
    )


@tool(response_format="content_and_artifact")
def find_amendments(
    sha256: str, section_number: str | None = None, section_position: int | None = None
) -> tuple[str, dict[str, Any]]:
    """Hitta ändringar."""
    changes = {SHA["a"]: [hit(*AMENDING)], SHA["b"]: [hit("a", 41, "6.21.9")]}
    amendments = [{"amending": place} for place in changes.get(sha256, [])]
    return structured({"amendments": amendments, "held_back": 0})


TOOLS: list[BaseTool] = [search_register, search_documents, read_section, find_amendments]


def build(model: NamedModel, tools: list[BaseTool] = TOOLS, limit: int = 16) -> AvtalAgent:
    settings = Settings(_env_file=None, validation_retries=1, agent_model_call_limit=limit)
    mcp = McpTools(
        tools=tools,
        reader=DictReader([SECTION]),
        register=ListRegister(),
        amendments=DictAmendments(),
    )
    return build_workflow(
        model,
        mcp,
        ScriptedReviewer(),
        InMemorySaver(serde=serializer()),
        settings,
        today=lambda: TODAY,
    )


async def ask(graph: AvtalAgent) -> QuestionRun:
    return await run_question(
        graph, QUESTION, thread_id=f"t-{uuid.uuid4()}", timeout=30, redact=lambda text: text
    )


def read(file: str, position: int, number: str | None) -> Step:
    args: dict[str, Any] = {"sha256": SHA.get(file, file), "section_position": position}
    if number:
        args["section_number"] = number
    return Step("read_section", args)


def plan(**changes: Any) -> AIMessage:
    return tool_call("QueryPlan", PLAN | changes, "plan")


# --- the fixed steps --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_the_workflow_runs_the_fixed_steps_in_order_and_records_them_as_steps() -> None:
    model = NamedModel(script=[plan(), final_answer("Tre månader [1].", [GOOD], call_id="c1")])

    run = await ask(build(model))

    first = [("a", 41, "6.21.9"), ("b", 10, "2.1"), ("c", 5, None), ("d", 7, "3.3")]
    first.append(("e", 8, "4.4"))
    filters = {"framework_area": "IT-drift"}
    qa = {"query": "uppsägning av kontrakt", **filters, "document_type": QUESTIONS_AND_ANSWERS}
    assert [(step.name, step.args) for step in run.steps] == [
        ("search_register", {"framework_area": "IT-drift", "sub_area": "IT-drift Mindre"}),
        ("search_documents", {"query": "uppsägningstid kontrakt", **filters}),
        *[("read_section", read(*place).args) for place in first],
        *[("find_amendments", {"sha256": SHA[f], "section_position": at}) for f, at, _ in first],
        ("read_section", read(*AMENDING).args),
        ("search_documents", qa),
        *[("read_section", read(QA[n], 1, None).args) for n in "123"],
        ("FinalAnswer", {"answered": True, "text": "Tre månader [1].", "citations": [GOOD]}),
    ]
    amending = run.steps[2 + 2 * MAX_SECTIONS]
    assert amending.target_from == "amendment"  # the report marks it Δ, as the agent's
    assert all(step.found_by_search for step in run.steps[2 : 2 + MAX_SECTIONS])
    assert not any(step.error for step in run.steps)
    assert run.answer is not None and run.answer.status == "verified"
    assert run.tools.count("read_section") == MAX_SECTIONS + 1 + MAX_ANSWERS
    assert run.asked == ()


@pytest.mark.anyio
async def test_the_plans_call_is_counted_and_its_tokens_reach_the_runs_callbacks() -> None:
    model = NamedModel(script=[plan(), final_answer("Tre månader [1].", [GOOD], call_id="c1")])

    run = await ask(build(model))

    assert run.model_calls == 2  # the plan and the draft, as ModelCallLimitMiddleware counts
    assert run.usage == {"the-agent-model": TokenUse(calls=2)}


@pytest.mark.anyio
async def test_the_plans_call_counts_against_the_limit() -> None:
    model = NamedModel(script=[plan(), final_answer("Tre år [1].", [BAD], call_id="c1")])

    run = await ask(build(model, limit=2))

    # The draft was sent back, and the limit (the plan and the draft) ended the run.
    assert run.model_calls == 2
    assert run.answer is not None and run.answer.status == "with_reservation"
    assert len(model.calls) == 2


@pytest.mark.anyio
async def test_the_model_is_offered_no_tool_but_the_answer() -> None:
    model = NamedModel(script=[plan(), final_answer("Tre månader [1].", [GOOD], call_id="c1")])

    await ask(build(model))

    assert model.bound[0] == ["QueryPlan"]  # the plan's structured call
    assert model.bound[1:] and all(names == ["FinalAnswer"] for names in model.bound[1:])
    system = model.calls[1][0]
    assert isinstance(system, SystemMessage) and system.content == workflow_prompt(TODAY)


@pytest.mark.anyio
async def test_the_answer_check_still_sends_a_bad_draft_back() -> None:
    model = NamedModel(
        script=[
            plan(),
            final_answer("Tre år [1].", [BAD], call_id="c1"),
            final_answer("Tre (3) månader [1].", [GOOD], call_id="c2"),
        ]
    )

    run = await ask(build(model))

    assert run.check_retries == 1
    assert [step.draft for step in run.steps if step.name == "FinalAnswer"] == [
        "sent_back",
        "submitted",
    ]
    assert run.rejections[0].rules == ("citations",)
    assert run.answer is not None and run.answer.status == "verified"
    assert run.model_calls == 3


@pytest.mark.anyio
async def test_a_tool_error_is_recorded_and_the_workflow_goes_on() -> None:
    @tool(response_format="content_and_artifact")
    def read_section(
        sha256: str, section_number: str | None = None, section_position: int | None = None
    ) -> tuple[str, dict[str, Any]]:
        """Läs ett avsnitt."""
        if sha256 == SHA["c"]:
            raise ToolException("Avsnittet hålls tillbaka i granskningen.")
        return structured({"sha256": sha256, "section_position": section_position, "text": TEXT})

    @tool
    def find_amendments(
        sha256: str, section_number: str | None = None, section_position: int | None = None
    ) -> str:
        """Hitta ändringar."""
        raise ToolException("Databasen svarar inte.")

    tools: list[BaseTool] = [search_documents, read_section, find_amendments]  # no register
    model = NamedModel(script=[plan(), final_answer("Tre månader [1].", [GOOD], call_id="c1")])

    run = await ask(build(model, tools))

    # The server has no search_register, c's section is held back, and find_amendments fails
    # on each of the four sections that were read; every step after them is made all the same.
    errors = [(step.name, step.args.get("sha256")) for step in run.steps if step.error]
    assert errors == [
        ("search_register", None),
        ("read_section", SHA["c"]),
        *[("find_amendments", SHA[f]) for f in "abde"],
    ]
    assert run.tool_errors == 6
    qa_reads = [step.args["sha256"] for step in run.steps[-4:-1]]
    assert qa_reads == [SHA["g"], QA["1"], QA["2"]]  # g was not read as an amendment this time
    assert run.steps[-1].name == "FinalAnswer"
    assert run.answer is not None and run.answer.status == "verified"


@pytest.mark.anyio
async def test_a_plan_that_does_not_parse_searches_with_the_question_without_filters() -> None:
    model = NamedModel(
        script=[AIMessage("Ingen plan."), final_answer("Tre månader [1].", [GOOD], call_id="c1")]
    )

    run = await ask(build(model))

    assert run.steps[0] == Step("search_documents", {"query": QUESTION}, found_by_search=False)
    assert "search_register" not in run.tools
    qa = next(s for s in run.steps[1:] if s.name == "search_documents")
    assert qa.args == {"query": QUESTION, "document_type": QUESTIONS_AND_ANSWERS}
    assert run.answer is not None and run.answer.status == "verified"


def test_the_limits_are_the_documented_ones() -> None:
    assert (MAX_SECTIONS, MAX_AMENDING, MAX_ANSWERS) == (5, 5, 3)
    assert len(FIXED_STEPS) == 6


# --- the prompt -------------------------------------------------------------------------------


def test_the_prompt_repeats_the_agents_own_answer_rules_word_for_word() -> None:
    rules = shared_rules()
    template = workflow_template()

    for part in (
        rules.role,
        rules.latest_wording,
        rules.not_in_material,
        rules.data_not_instructions,
    ):
        assert part and part in SYSTEM_PROMPT and part in template
    assert rules.answer.startswith("Svaret\n- Lämna svaret med FinalAnswer")
    assert rules.answer.removeprefix("Svaret\n") in SYSTEM_PROMPT and rules.answer in template
    assert "Sätt [n] direkt efter varje påstående" in rules.answer
    assert "citera ordagrant" in rules.answer
    assert rules.latest_wording.startswith("Har det ändrats, bygg svaret på den senaste lydelsen")
    assert rules.not_in_material.startswith("Svara på det som framgår")
    assert "Du kan inte anropa några verktyg själv" in template
    assert "{" not in template
    assert workflow_prompt(TODAY).endswith("\n\nDagens datum: 2026-10-07.")


def test_a_changed_system_prompt_stops_the_prompt_from_being_built() -> None:
    with pytest.raises(ValueError, match="Svaret"):
        shared_rules(SYSTEM_PROMPT.replace("\n\nSvaret\n", "\n\nSvar\n"))
    with pytest.raises(ValueError, match="latest-wording"):
        shared_rules(SYSTEM_PROMPT.replace("Har det ändrats", "Om det ändrats"))


def test_the_plans_areas_are_the_gold_questions_areas() -> None:
    gold = load_gold(Path("evals/datasets/gold_sv.jsonl"))

    assert {question.area for question in gold.questions} <= set(PILOT_AREAS)
    for question in gold.questions:
        assert question.scope.framework_area in (None, *PILOT_AREAS)


def test_a_result_without_structure_reads_as_no_hits() -> None:
    message = ToolMessage("inte json", tool_call_id="x", name="search_documents")

    assert _hits([message]) == []
