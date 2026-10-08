"""Tests for avtalsagent.agent.graph and .middleware: the agent's graph run offline.

A scripted chat model (scripted_model.py) plays the model's part, fake tools
the avtal-mcp tools, dicts the section and amendment readers, a list the
register and a scripted reviewer the reviewer model, so no test needs a
network, an API key or a database. The checkpoints are kept in memory with
the agent's serializer, as on the command line. Every run is async (`ainvoke`,
`astream`), as in the command line and the API; one test shows that a
synchronous run stops before the first model call.
"""

from collections.abc import Callable
from datetime import date
from typing import Any, cast

import pytest
from ag_ui_langgraph.interrupts import DEFAULT_RESUME_SENTINEL_CANCELLED
from langchain.agents.middleware import InputAgentState
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, StructuredTool, ToolException, tool
from langchain_mcp_adapters.tools import convert_mcp_tool_to_langchain_tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from mcp import ClientSession
from mcp.types import CallToolResult, TextContent
from mcp.types import Tool as MCPTool
from pydantic import ValidationError

from avtalsagent.agent.amendments import AmendmentInfo
from avtalsagent.agent.ask_user import CANCELLED_MARK, NOT_ANSWERED
from avtalsagent.agent.checkpointer import serializer
from avtalsagent.agent.graph import AGENT_NAME, ANSWER_SUBMITTED, AvtalAgent, build_agent
from avtalsagent.agent.mcp_tools import McpTools
from avtalsagent.agent.middleware import (
    NO_DRAFT_TEXT,
    SYNC_RUN_ERROR,
    UNCHECKED_NOTE,
    AnswerCheck,
    _answer,
)
from avtalsagent.agent.register_reader import RegisterEntry
from avtalsagent.agent.schemas import Answer, FinalAnswer, RegisterFact
from avtalsagent.agent.sections import CitedSection
from avtalsagent.config import Settings
from avtalsagent.validation.chain import (
    MARKERS_NOTE,
    NO_SOURCE_NOTE,
    NOT_REVIEWED_NOTE,
    ChainReport,
)
from avtalsagent.validation.review import (
    REVIEW_FAILED_NOTE,
    ClaimReview,
    FollowUp,
    ReviewVerdict,
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

SHA = "a1" * 32
OTHER_SHA = "b2" * 32
SECTION = CitedSection(
    sha256=SHA,
    section_position=41,
    section_number="6.21.9",
    section_title="Uppsägning",
    file_title="Allmänna villkor",
    page_titles=["IT-drift Större, fler än 200 anställda", "IT-drift Mindre"],
    page_start=14,
    text="Kontraktet kan sägas upp av Kunden med en uppsägningstid om tre (3) månader. "
    "Uppsägning ska ske skriftligen.",
)
GOOD = {
    "id": 1,
    "sha256": SHA,
    "section_position": 41,
    "quote": "uppsägningstid om tre (3) månader",
}
BAD = {"id": 1, "sha256": SHA, "section_position": 41, "quote": "uppsägningstid på tre månader"}
TODAY = date(2026, 10, 7)
ASK_WHICH = {
    "question": "Vilket avtal menar du?",
    "options": ["IT-drift Större", "IT-drift Mindre"],
}
NOT_FOUND = (
    "Det finns inget avsnitt med nummer 99.9 i dokumentet. Kontrollera numret med get_outline."
)
# Nordlo Advance's agreement on IT-drift Mindre, as the public register has it (gold q10).
NORDLO = RegisterEntry(
    agreement_number="23.3-5890-2023-002",
    procurement_number="23.3-5890-2023",
    supplier_name="Nordlo Advance AB",
    org_number="556486-1689",
    former_names=["EPM Data"],
    framework_area="IT-drift",
    sub_area="IT-drift Mindre",
    valid_from=date(2024, 11, 14),
    valid_to=date(2028, 11, 13),
    max_extension_to=None,
)
NORDLO_TEXT = (
    "Nordlo Advance AB har avtal 23.3-5890-2023-002 på IT-drift Mindre, organisationsnummer "
    "556486-1689. Avtalet gäller {} enligt registret."
)
SIX_MONTHS = ReviewVerdict(
    claims=[
        ClaimReview(
            claim="Uppsägningstiden är sex månader",
            citation_ids=[1],
            support="unsupported",
            reason="Avsnittet anger tre månader.",
        )
    ],
    missing=[],
)


def ask(question: str) -> InputAgentState:
    return {"messages": [{"role": "user", "content": question}]}


QUESTION = ask("Vilken uppsägningstid gäller?")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@tool
def search_documents(query: str) -> str:
    """Sök i dokumenten."""
    return f"Träff: sha256 {SHA}, section_position 41, avsnitt 6.21.9 Uppsägning."


def build(
    script: list[AIMessage],
    *,
    tools: list[BaseTool] | None = None,
    reader: DictReader | None = None,
    register: ListRegister | None = None,
    amendments: DictAmendments | None = None,
    reviewer: ScriptedReviewer | None = None,
    retries: int = 1,
    limit: int = 16,
    today: Callable[[], date] = lambda: TODAY,
) -> tuple[AvtalAgent, ScriptedModel]:
    model = ScriptedModel(script=script)
    settings = Settings(_env_file=None, validation_retries=retries, agent_model_call_limit=limit)
    mcp = McpTools(
        tools=[search_documents] if tools is None else tools,
        reader=reader or DictReader([SECTION]),
        register=register or ListRegister([NORDLO]),
        amendments=amendments or DictAmendments(),
    )
    graph = build_agent(
        model,
        mcp,
        reviewer or ScriptedReviewer(),
        InMemorySaver(serde=serializer()),
        settings,
        today=today,
    )
    return graph, model


THREAD: RunnableConfig = {"configurable": {"thread_id": "t1"}}


def tool_messages(messages: list[BaseMessage], name: str) -> list[ToolMessage]:
    return [m for m in messages if isinstance(m, ToolMessage) and m.name == name]


# --- the graph ----------------------------------------------------------------------


def test_the_graph_takes_only_messages_and_shows_the_answer() -> None:
    graph, _ = build([])

    assert graph.name == AGENT_NAME
    assert list(graph.get_input_jsonschema()["properties"]) == ["messages"]
    assert "answer" in graph.get_output_jsonschema()["properties"]
    assert "validation_retries" not in graph.get_output_jsonschema()["properties"]
    assert "fallback_answer" not in graph.get_output_jsonschema()["properties"]


@pytest.mark.anyio
async def test_the_model_gets_the_tools_ask_user_and_final_answer() -> None:
    graph, model = build([final_answer("Det framgår inte.", answered=False, call_id="c1")])

    await graph.ainvoke(QUESTION, THREAD)

    assert model.bound == [["search_documents", "ask_user", "FinalAnswer"]]


@pytest.mark.anyio
async def test_the_system_prompt_has_the_date_of_each_run() -> None:
    days = [date(2026, 10, 7), date(2026, 10, 8)]
    graph, model = build(
        [
            final_answer("Det framgår inte.", answered=False, call_id="c1"),
            final_answer("Det framgår inte.", answered=False, call_id="c2"),
        ],
        today=lambda: days[0],
    )

    await graph.ainvoke(QUESTION, THREAD)
    days.pop(0)
    await graph.ainvoke(QUESTION, THREAD)

    prompts = [call[0] for call in model.calls]
    assert all(isinstance(prompt, SystemMessage) for prompt in prompts)
    assert prompts[0].text.endswith("Dagens datum: 2026-10-07.")
    assert prompts[1].text.endswith("Dagens datum: 2026-10-08.")


# --- the citation check -------------------------------------------------------------


@pytest.mark.anyio
async def test_a_draft_that_passes_is_verified_with_fields_from_the_section() -> None:
    reader = DictReader([SECTION])
    reviewer = ScriptedReviewer()
    graph, model = build(
        [
            tool_call("search_documents", {"query": "uppsägningstid"}, "c1"),
            final_answer("Tre månader [1].", [GOOD], call_id="c2"),
        ],
        reader=reader,
        reviewer=reviewer,
    )

    result = await graph.ainvoke(QUESTION, THREAD)

    answer = result["answer"]
    assert isinstance(answer, Answer)
    assert answer.status == "verified"
    assert answer.text == "Tre månader [1]."
    [citation] = answer.citations
    assert citation.model_dump() == {
        "id": 1,
        "sha256": SHA,
        "file_title": "Allmänna villkor",
        "page_title": "IT-drift Större, fler än 200 anställda",
        "section_number": "6.21.9",
        "section_title": "Uppsägning",
        "page": 14,
        "quote": "uppsägningstid om tre (3) månader",
        "verified": True,
        "source": "framework",
        "upload_id": None,
    }
    assert (answer.reservations, answer.register_facts) == ([], [])
    assert reader.reads == [(SHA, 41)]
    [submitted] = tool_messages(result["messages"], "FinalAnswer")
    assert submitted.content == ANSWER_SUBMITTED
    assert len(model.calls) == 2
    # The reviewer read the question, the answer and the section as the check read it.
    [request] = reviewer.requests
    assert (request.question, request.follow_ups) == ("Vilken uppsägningstid gäller?", [])
    assert (request.answer_text, request.today) == ("Tre månader [1].", TODAY)
    [source] = request.sources
    assert (source.id, source.section_number, source.text) == (1, "6.21.9", SECTION.text)
    assert source.page_titles == SECTION.page_titles


@pytest.mark.anyio
async def test_a_failed_draft_goes_back_to_the_model_and_the_retry_is_verified() -> None:
    graph, model = build(
        [
            final_answer("Tre månader [1].", [BAD], call_id="c1"),
            final_answer("Tre månader [1].", [GOOD], call_id="c2"),
        ]
    )

    nodes = [
        node
        async for update in graph.astream(QUESTION, THREAD, stream_mode="updates")
        for node in update
    ]

    state = (await graph.aget_state(THREAD)).values
    assert state["answer"].status == "verified"
    assert state["validation_retries"] == 1
    assert nodes.count("AnswerCheck.after_agent") == 2
    assert nodes.count("model") == 2
    # The feedback took the place of the first draft's tool result: same call, status error.
    first, second = tool_messages(state["messages"], "FinalAnswer")
    assert first.tool_call_id == "c1"
    assert first.status == "error"
    assert isinstance(first.content, str)
    assert first.content.startswith("Kontrollen underkände svaret (försök 1 av 2):\n- Källa [1]:")
    assert "finns inte ordagrant i avsnitt 6.21.9 (Uppsägning)" in first.content
    assert (second.tool_call_id, second.status, second.content) == (
        "c2",
        "success",
        ANSWER_SUBMITTED,
    )
    # The model read the feedback as a tool result; the user wrote only the question.
    assert model.calls[1][-1] == first
    assert sum(isinstance(m, HumanMessage) for m in state["messages"]) == 1


@pytest.mark.anyio
async def test_after_the_retries_the_answer_is_given_with_reservation() -> None:
    two_sources = [GOOD, {**BAD, "id": 2}]
    graph, model = build(
        [
            final_answer("Tre månader [1], skriftligen [2].", two_sources, call_id="c1"),
            final_answer("Tre månader [1], skriftligen [2].", two_sources, call_id="c2"),
        ],
        retries=1,
    )

    result = await graph.ainvoke(QUESTION, THREAD)

    answer = result["answer"]
    assert answer.status == "with_reservation"
    assert [(c.id, c.verified) for c in answer.citations] == [(1, True), (2, False)]
    assert answer.citations[1].file_title == "Allmänna villkor"
    # The claim [1] supports was never reviewed either, and the user is told so.
    assert answer.reservations == [
        "Källa [2] kunde inte kontrolleras mot avtalstexten.",
        NOT_REVIEWED_NOTE,
    ]
    assert len(model.calls) == 2


@pytest.mark.anyio
async def test_with_no_retries_a_failed_draft_is_given_with_reservation_at_once() -> None:
    graph, model = build([final_answer("Tre månader [1].", [BAD], call_id="c1")], retries=0)

    result = await graph.ainvoke(QUESTION, THREAD)

    assert result["answer"].status == "with_reservation"
    assert [c.verified for c in result["answer"].citations] == [False]
    assert len(model.calls) == 1


@pytest.mark.anyio
async def test_each_cited_section_is_read_once() -> None:
    reader = DictReader([SECTION])
    second = {**GOOD, "id": 2, "quote": "Uppsägning ska ske skriftligen."}
    graph, _ = build(
        [final_answer("Tre månader [1], skriftligen [2].", [GOOD, second], call_id="c1")],
        reader=reader,
    )

    result = await graph.ainvoke(QUESTION, THREAD)

    assert result["answer"].status == "verified"
    assert reader.reads == [(SHA, 41)]


@pytest.mark.anyio
async def test_a_section_the_reader_cannot_read_fails_the_check() -> None:
    elsewhere = {**GOOD, "sha256": OTHER_SHA}
    graph, _ = build([final_answer("Tre månader [1].", [elsewhere], call_id="c1")], retries=0)

    result = await graph.ainvoke(QUESTION, THREAD)

    [citation] = result["answer"].citations
    assert result["answer"].status == "with_reservation"
    assert (citation.sha256, citation.file_title, citation.verified) == (OTHER_SHA, "", False)


@pytest.mark.anyio
async def test_an_answer_the_agreements_do_not_give_is_no_answer_in_the_models_words() -> None:
    reader = DictReader([SECTION])
    text = "Avtalen säger inget om uppsägning under semestern."
    graph, _ = build([final_answer(text, answered=False, call_id="c1")], reader=reader)

    result = await graph.ainvoke(QUESTION, THREAD)

    assert result["answer"] == Answer(text=text, status="no_answer", citations=[])
    assert reader.reads == []


@pytest.mark.anyio
async def test_a_no_answer_keeps_its_sources_and_they_are_checked() -> None:
    # "Not in the agreements" can still cite where the matter is left to the contract.
    reader = DictReader([SECTION])
    text = "Avtalen anger ingen tid för uppsägning under semestern, bara tre månader [1]."
    graph, _ = build([final_answer(text, [GOOD], answered=False, call_id="c1")], reader=reader)

    result = await graph.ainvoke(QUESTION, THREAD)

    answer = result["answer"]
    assert (answer.text, answer.status) == (text, "no_answer")
    assert [(c.id, c.file_title, c.verified) for c in answer.citations] == [
        (1, "Allmänna villkor", True)
    ]
    assert reader.reads == [(SHA, 41)]


@pytest.mark.anyio
async def test_a_no_answer_with_a_marker_and_no_source_goes_back_to_the_model() -> None:
    # The live test's case: [1] in the text, the source left out, so the user saw a dead [1].
    graph, model = build(
        [
            final_answer("Det framgår inte [1].", answered=False, call_id="c1"),
            final_answer("Det framgår inte.", answered=False, call_id="c2"),
        ]
    )

    result = await graph.ainvoke(QUESTION, THREAD)

    assert result["answer"] == Answer(text="Det framgår inte.", status="no_answer", citations=[])
    assert len(model.calls) == 2
    first, _ = tool_messages(result["messages"], "FinalAnswer")
    assert "Hänvisningen [1] i texten har ingen källa med id 1." in str(first.content)


@pytest.mark.anyio
async def test_a_no_answer_whose_source_still_fails_keeps_it_unverified() -> None:
    graph, model = build(
        [final_answer("Det framgår inte [1].", [BAD], answered=False, call_id="c1")], retries=0
    )

    result = await graph.ainvoke(QUESTION, THREAD)

    assert result["answer"].status == "no_answer"
    assert [c.verified for c in result["answer"].citations] == [False]
    assert len(model.calls) == 1


@pytest.mark.anyio
async def test_a_follow_up_question_gets_its_own_new_attempt() -> None:
    graph, model = build(
        [
            final_answer("Tre månader [1].", [BAD], call_id="c1"),
            final_answer("Tre månader [1].", [GOOD], call_id="c2"),
            final_answer("Skriftligen [1].", [BAD], call_id="c3"),
            final_answer("Skriftligen [1].", [GOOD], call_id="c4"),
        ]
    )

    await graph.ainvoke(QUESTION, THREAD)
    second = await graph.ainvoke(ask("Hur ska den ske?"), THREAD)

    assert second["answer"].status == "verified"
    assert len(model.calls) == 4


@pytest.mark.anyio
async def test_a_refused_draft_beside_another_tool_call_goes_back_to_the_model() -> None:
    # ToolStrategy refuses the malformed hash; the search beside it ended the loop all the same.
    refused = {**GOOD, "sha256": "XYZ"}
    both = AIMessage(
        content="",
        tool_calls=[
            {"name": "search_documents", "args": {"query": "uppsägning"}, "id": "c1"},
            {
                "name": "FinalAnswer",
                "args": {"answered": True, "text": "Tre månader [1].", "citations": [refused]},
                "id": "c2",
            },
        ],
    )
    graph, model = build([both, final_answer("Tre månader [1].", [GOOD], call_id="c3")])

    result = await graph.ainvoke(QUESTION, THREAD)

    assert result["answer"].status == "verified"
    assert len(model.calls) == 2
    # The second model call saw why the first draft was refused.
    assert any("Failed to parse" in str(m.content) for m in model.calls[1])


@pytest.mark.anyio
async def test_refused_drafts_beside_tool_calls_still_end_at_the_limit() -> None:
    refused = {**GOOD, "sha256": "XYZ"}

    def both(n: int) -> AIMessage:
        return AIMessage(
            content="",
            tool_calls=[
                {"name": "search_documents", "args": {"query": "uppsägning"}, "id": f"s{n}"},
                {
                    "name": "FinalAnswer",
                    "args": {"answered": True, "text": "Tre [1].", "citations": [refused]},
                    "id": f"f{n}",
                },
            ],
        )

    graph, model = build([both(n) for n in range(4)], limit=3)

    result = await graph.ainvoke(QUESTION, THREAD)

    assert result["answer"] == Answer(text=NO_DRAFT_TEXT, status="no_answer", citations=[])
    assert len(model.calls) == 3


@pytest.mark.anyio
async def test_an_answer_without_sources_is_given_with_reservation() -> None:
    graph, model = build([final_answer("Uppsägningstiden är tre månader.", call_id="c1")])

    result = await graph.ainvoke(QUESTION, THREAD)

    assert result["answer"].status == "with_reservation"
    assert result["answer"].citations == []
    assert result["answer"].reservations == [NO_SOURCE_NOTE]
    assert len(model.calls) == 1


@pytest.mark.anyio
async def test_the_model_call_limit_ends_the_question_as_no_answer() -> None:
    graph, model = build(
        [tool_call("search_documents", {"query": f"sökning {n}"}, f"c{n}") for n in range(3)],
        limit=2,
    )

    result = await graph.ainvoke(QUESTION, THREAD)

    assert result["answer"] == Answer(text=NO_DRAFT_TEXT, status="no_answer", citations=[])
    assert len(model.calls) == 2
    # The limit's English note in the chat is replaced by the answer's words.
    last = result["messages"][-1]
    assert isinstance(last, AIMessage)
    assert last.content == NO_DRAFT_TEXT
    assert not any("limit" in str(m.content) for m in result["messages"])


@pytest.mark.anyio
async def test_a_limit_reached_after_feedback_gives_the_last_drafts_answer() -> None:
    # The new attempt counts against the limit; the failed draft is still better than nothing.
    graph, model = build([final_answer("Tre månader [1].", [BAD], call_id="c1")], limit=1)

    result = await graph.ainvoke(QUESTION, THREAD)

    answer = result["answer"]
    assert (answer.text, answer.status) == ("Tre månader [1].", "with_reservation")
    assert [c.verified for c in answer.citations] == [False]
    assert answer.reservations == [
        "Källa [1] kunde inte kontrolleras mot avtalstexten.",
        NOT_REVIEWED_NOTE,
    ]
    assert len(model.calls) == 1
    # The limit's note is replaced by the answer's words, and the fallback is not kept.
    assert result["messages"][-1].content == "Tre månader [1]."
    assert (await graph.aget_state(THREAD)).values["fallback_answer"] is None


def test_retries_cannot_be_negative() -> None:
    with pytest.raises(ValueError, match="retries"):
        AnswerCheck(
            DictReader([]), ListRegister(), DictAmendments(), ScriptedReviewer(), -1, lambda: TODAY
        )


def test_a_synchronous_run_stops_before_the_model_is_called() -> None:
    graph, model = build([final_answer("Tre månader [1].", [GOOD], call_id="c1")])

    with pytest.raises(RuntimeError) as raised:
        graph.invoke(QUESTION, THREAD)

    assert str(raised.value) == SYNC_RUN_ERROR
    assert model.calls == []


# --- the register facts and the review (M8) -----------------------------------------


@pytest.mark.anyio
async def test_an_answer_from_the_register_alone_is_verified_with_its_rows() -> None:
    register = ListRegister([NORDLO])
    reviewer = ScriptedReviewer()
    text = NORDLO_TEXT.format("2024-11-14–2028-11-13")
    graph, model = build(
        [final_answer(text, register_facts=["23.3-5890-2023-002"], call_id="c1")],
        register=register,
        reviewer=reviewer,
    )

    result = await graph.ainvoke(QUESTION, THREAD)

    answer = result["answer"]
    assert (answer.status, answer.citations, answer.reservations) == ("verified", [], [])
    assert answer.register_facts == [
        RegisterFact(
            agreement_number="23.3-5890-2023-002",
            supplier_name="Nordlo Advance AB",
            former_names=["EPM Data"],
            org_number="556486-1689",
            sub_area="IT-drift Mindre",
            valid_from=date(2024, 11, 14),
            valid_to=date(2028, 11, 13),
            max_extension_to=None,
        )
    ]
    assert register.reads == ["23.3-5890-2023-002"]
    assert reviewer.requests[0].register_facts == answer.register_facts
    assert len(model.calls) == 1


@pytest.mark.anyio
async def test_a_wrong_date_goes_back_to_the_model_and_the_fixed_answer_is_verified() -> None:
    graph, model = build(
        [
            final_answer(
                NORDLO_TEXT.format("2024-11-14–2028-11-14"),
                register_facts=["23.3-5890-2023-002"],
                call_id="c1",
            ),
            final_answer(
                NORDLO_TEXT.format("2024-11-14–2028-11-13"),
                register_facts=["23.3-5890-2023-002"],
                call_id="c2",
            ),
        ]
    )

    result = await graph.ainvoke(QUESTION, THREAD)

    assert result["answer"].status == "verified"
    first, _ = tool_messages(result["messages"], "FinalAnswer")
    assert first.status == "error"
    assert "2028-11-14" in first.text
    assert "search_register" in first.text
    assert len(model.calls) == 2


@pytest.mark.anyio
async def test_a_date_still_wrong_after_two_retries_is_given_with_a_note() -> None:
    wrong = NORDLO_TEXT.format("2024-11-14–2028-11-14")
    graph, model = build(
        [
            final_answer(wrong, register_facts=["23.3-5890-2023-002"], call_id=f"c{n}")
            for n in range(3)
        ],
        retries=2,
    )

    result = await graph.ainvoke(QUESTION, THREAD)

    answer = result["answer"]
    assert (answer.text, answer.status) == (wrong, "with_reservation")
    assert answer.reservations == [
        "Kunde inte kontrolleras mot registret: 2028-11-14.",
        NOT_REVIEWED_NOTE,
    ]
    assert [fact.agreement_number for fact in answer.register_facts] == ["23.3-5890-2023-002"]
    assert len(model.calls) == 3
    feedback = [m.text for m in tool_messages(result["messages"], "FinalAnswer")]
    assert feedback[0].startswith("Kontrollen underkände svaret (försök 1 av 3):")
    assert feedback[1].startswith("Kontrollen underkände svaret (försök 2 av 3):")
    assert feedback[2] == ANSWER_SUBMITTED


@pytest.mark.anyio
async def test_a_register_fact_from_an_undeclared_agreement_goes_back_to_the_model() -> None:
    text = NORDLO_TEXT.format("2024-11-14–2028-11-13")
    graph, model = build(
        [
            final_answer(text, call_id="c1"),
            final_answer(text, register_facts=["23.3-5890-2023-002"], call_id="c2"),
        ]
    )

    result = await graph.ainvoke(QUESTION, THREAD)

    assert result["answer"].status == "verified"
    first, _ = tool_messages(result["messages"], "FinalAnswer")
    assert "register_facts" in first.text
    assert len(model.calls) == 2


@pytest.mark.anyio
async def test_the_reviewer_stops_an_answer_its_source_does_not_support() -> None:
    # The quote is word for word, so only the reviewer sees that the text says six months.
    reviewer = ScriptedReviewer([SIX_MONTHS])
    graph, model = build(
        [
            final_answer("Uppsägningstiden är sex månader [1].", [GOOD], call_id="c1"),
            final_answer("Uppsägningstiden är tre månader [1].", [GOOD], call_id="c2"),
        ],
        reviewer=reviewer,
    )

    result = await graph.ainvoke(QUESTION, THREAD)

    assert result["answer"].status == "verified"
    assert result["answer"].text == "Uppsägningstiden är tre månader [1]."
    first, _ = tool_messages(result["messages"], "FinalAnswer")
    assert first.status == "error"
    assert "Avsnittet anger tre månader." in first.text
    assert [r.answer_text for r in reviewer.requests] == [
        "Uppsägningstiden är sex månader [1].",
        "Uppsägningstiden är tre månader [1].",
    ]
    assert len(model.calls) == 2


@pytest.mark.anyio
async def test_a_failed_citation_is_sent_back_without_a_review() -> None:
    reviewer = ScriptedReviewer()
    graph, _ = build(
        [
            final_answer("Tre månader [1].", [BAD], call_id="c1"),
            final_answer("Tre månader [1].", [GOOD], call_id="c2"),
        ],
        reviewer=reviewer,
    )

    result = await graph.ainvoke(QUESTION, THREAD)

    assert result["answer"].status == "verified"
    # Only the second draft, the one whose quote passed, was reviewed.
    assert [r.sources[0].id for r in reviewer.requests] == [1]
    assert len(reviewer.requests) == 1


@pytest.mark.anyio
async def test_the_rules_and_the_reviewer_share_the_retries() -> None:
    reviewer = ScriptedReviewer([SIX_MONTHS, SIX_MONTHS])
    six = "Uppsägningstiden är sex månader [1]."
    graph, model = build(
        [
            final_answer(six, [BAD], call_id="c1"),  # the citation rule fails it
            final_answer(six, [GOOD], call_id="c2"),  # the reviewer fails it
            final_answer(six, [GOOD], call_id="c3"),  # the reviewer fails it, no retry left
        ],
        reviewer=reviewer,
        retries=2,
    )

    result = await graph.ainvoke(QUESTION, THREAD)

    answer = result["answer"]
    assert (answer.text, answer.status) == (six, "with_reservation")
    assert [c.verified for c in answer.citations] == [True]
    assert answer.reservations != []
    assert all("sex månader" in note or "stöd" in note for note in answer.reservations)
    assert len(model.calls) == 3
    assert len(reviewer.requests) == 2


@pytest.mark.anyio
async def test_a_review_that_fails_gives_the_answer_with_a_note_and_no_new_attempt() -> None:
    graph, model = build(
        [final_answer("Tre månader [1].", [GOOD], call_id="c1")],
        reviewer=ScriptedReviewer([None]),
    )

    result = await graph.ainvoke(QUESTION, THREAD)

    answer = result["answer"]
    assert answer.status == "with_reservation"
    assert [c.verified for c in answer.citations] == [True]
    assert answer.reservations == [REVIEW_FAILED_NOTE]
    assert len(model.calls) == 1


@pytest.mark.anyio
async def test_an_answer_the_agreements_do_not_give_is_not_reviewed() -> None:
    reviewer = ScriptedReviewer()
    graph, _ = build(
        [final_answer("Det framgår inte av avtalen.", answered=False, call_id="c1")],
        reviewer=reviewer,
    )

    result = await graph.ainvoke(QUESTION, THREAD)

    assert result["answer"].status == "no_answer"
    assert reviewer.requests == []


@pytest.mark.anyio
async def test_the_reviewer_reads_the_users_answer_to_ask_user() -> None:
    reviewer = ScriptedReviewer()
    graph, _ = build(
        [
            tool_call("ask_user", ASK_WHICH, "c1"),
            final_answer("Tre månader [1].", [GOOD], call_id="c2"),
        ],
        reviewer=reviewer,
    )

    await graph.ainvoke(QUESTION, THREAD)
    await graph.ainvoke(Command(resume="IT-drift Större"), THREAD)

    [request] = reviewer.requests
    assert (request.question, request.follow_ups) == (
        "Vilken uppsägningstid gäller?",
        [FollowUp(question="Vilket avtal menar du?", answer="IT-drift Större")],
    )


@pytest.mark.anyio
async def test_a_cancelled_question_is_not_an_answer_from_the_user() -> None:
    reviewer = ScriptedReviewer()
    graph, _ = build(
        [
            tool_call("ask_user", ASK_WHICH, "c1"),
            final_answer("Tre månader [1].", [GOOD], call_id="c2"),
        ],
        reviewer=reviewer,
    )

    await graph.ainvoke(QUESTION, THREAD)
    await graph.ainvoke(Command(resume={CANCELLED_MARK: True, "interrupt_id": "i1"}), THREAD)

    assert reviewer.requests[0].follow_ups == []


@pytest.mark.anyio
async def test_the_reviewer_reads_only_the_latest_question() -> None:
    reviewer = ScriptedReviewer()
    graph, _ = build(
        [
            final_answer("Tre månader [1].", [GOOD], call_id="c1"),
            final_answer(
                "Skriftligen [1].",
                [{**GOOD, "quote": "Uppsägning ska ske skriftligen."}],
                call_id="c2",
            ),
        ],
        reviewer=reviewer,
    )

    await graph.ainvoke(QUESTION, THREAD)
    await graph.ainvoke(ask("Hur ska den ske?"), THREAD)

    assert [r.question for r in reviewer.requests] == [
        "Vilken uppsägningstid gäller?",
        "Hur ska den ske?",
    ]


@pytest.mark.anyio
async def test_an_answer_with_reservation_always_says_why() -> None:
    # Declared but unused: a problem for the model, nothing the user needs named; but the
    # answer was not reviewed because of it, and that the user is told.
    text = "Uppsägningstiden är tre månader [1]."
    reviewer = ScriptedReviewer()
    graph, _ = build(
        [final_answer(text, [GOOD], register_facts=["23.3-5890-2023-002"], call_id="c1")],
        reviewer=reviewer,
        retries=0,
    )

    result = await graph.ainvoke(QUESTION, THREAD)

    answer = result["answer"]
    assert answer.status == "with_reservation"
    assert answer.reservations == [NOT_REVIEWED_NOTE]
    assert reviewer.requests == []


def test_a_report_with_problems_and_no_note_still_gives_the_user_a_reason() -> None:
    report = ChainReport(
        problems=["Något."],
        citations=[],
        register_facts=[],
        reservations=[],
        review_failed=False,
        amendments_unread=False,
        has_source=True,
    )
    draft = FinalAnswer(answered=True, text="Tre månader.", citations=[])

    assert _answer(draft, report).reservations == [UNCHECKED_NOTE]


@pytest.mark.anyio
async def test_a_failed_quote_and_a_dead_marker_are_both_named() -> None:
    text = "Tre månader [1], skriftligen [2], se [3]."
    sources = [BAD, {**GOOD, "id": 2}]
    graph, _ = build([final_answer(text, sources, call_id="c1")], retries=0)

    result = await graph.ainvoke(QUESTION, THREAD)

    assert result["answer"].reservations == [
        "Källa [1] kunde inte kontrolleras mot avtalstexten.",
        MARKERS_NOTE,
        NOT_REVIEWED_NOTE,
    ]


@pytest.mark.anyio
async def test_an_answer_without_sources_whose_review_fails_says_both() -> None:
    graph, _ = build(
        [final_answer("Uppsägningstiden är tre månader.", call_id="c1")],
        reviewer=ScriptedReviewer([None]),
    )

    result = await graph.ainvoke(QUESTION, THREAD)

    assert result["answer"].reservations == [NO_SOURCE_NOTE, REVIEW_FAILED_NOTE]


@pytest.mark.anyio
async def test_a_section_cited_twice_is_sent_to_the_reviewer_once() -> None:
    reviewer = ScriptedReviewer()
    twice = [GOOD, {**GOOD, "id": 2, "quote": "Uppsägning ska ske skriftligen."}]
    graph, _ = build(
        [final_answer("Tre månader [1], skriftligen [2].", twice, call_id="c1")],
        reviewer=reviewer,
    )

    await graph.ainvoke(QUESTION, THREAD)

    [request] = reviewer.requests
    assert [(s.id, s.same_as, s.text == SECTION.text) for s in request.sources] == [
        (1, None, True),
        (2, 1, False),
    ]
    assert request.sources[1].text == ""


# --- the latest wording -------------------------------------------------------------

# A correction of 6.21.9 in a questions log, as 7a765d649e25 §9 corrects punkt 3.2 of the
# tender documents for IT-drift Mindre.
CORRECTION = CitedSection(
    sha256=OTHER_SHA,
    section_position=6,
    section_number="9",
    section_title="Publik fråga",
    file_title="Frågor och svar",
    page_titles=["IT-drift Mindre"],
    page_start=2,
    text="Publikt svar 2024-02-20 08:39 Rättelse. Texten som gäller är följande för punkt "
    "6.21.9: ”Kontraktet kan sägas upp av Kunden med en uppsägningstid om sex (6) månader.”",
)
CHANGED = AmendmentInfo(
    sha256=OTHER_SHA,
    section_position=6,
    file_title="Frågor och svar",
    section_number="9",
    section_title="Publik fråga",
    amended_position=41,
    status="resolved",
    dated=date(2024, 2, 20),
)
CORRECTED = {
    "id": 2,
    "sha256": OTHER_SHA,
    "section_position": 6,
    "quote": "uppsägningstid om sex (6) månader",
}
CHANGED_NOTE = (
    "Källa [1] kan bygga på en lydelse som har ändrats senare: Frågor och svar, avsnitt 9 "
    "Publik fråga (2024-02-20)."
)


@pytest.mark.anyio
async def test_a_draft_on_a_changed_section_goes_back_and_citing_the_change_passes() -> None:
    amendments = DictAmendments({(SHA, 41): [CHANGED]})
    reviewer = ScriptedReviewer()
    corrected = "Tre månader [1], men efter en rättelse sex månader [2]."
    graph, model = build(
        [
            final_answer("Tre månader [1].", [GOOD], call_id="c1"),
            final_answer(corrected, [GOOD, CORRECTED], call_id="c2"),
        ],
        reader=DictReader([SECTION, CORRECTION]),
        amendments=amendments,
        reviewer=reviewer,
    )

    result = await graph.ainvoke(QUESTION, THREAD)

    answer = result["answer"]
    assert (answer.text, answer.status, answer.reservations) == (corrected, "verified", [])
    first, _ = tool_messages(result["messages"], "FinalAnswer")
    assert first.status == "error"
    assert (
        "Källa [1] (Allmänna villkor, avsnitt 6.21.9 Uppsägning) har ändrats av Frågor och "
        f"svar, avsnitt 9 Publik fråga (2024-02-20, sha256 {OTHER_SHA}, section_position 6)."
    ) in first.text
    # Only the second draft was reviewed, with both sections; each draft's sections were
    # looked up after their quotes passed.
    [request] = reviewer.requests
    assert [source.id for source in request.sources] == [1, 2]
    assert amendments.reads == [(SHA, 41), (SHA, 41), (OTHER_SHA, 6)]
    assert len(model.calls) == 2


@pytest.mark.anyio
async def test_a_changed_section_still_cited_alone_is_given_with_a_note() -> None:
    reviewer = ScriptedReviewer()
    graph, _ = build(
        [final_answer("Tre månader [1].", [GOOD], call_id="c1")],
        reader=DictReader([SECTION, CORRECTION]),
        amendments=DictAmendments({(SHA, 41): [CHANGED]}),
        reviewer=reviewer,
        retries=0,
    )

    result = await graph.ainvoke(QUESTION, THREAD)

    answer = result["answer"]
    assert answer.status == "with_reservation"
    assert [c.verified for c in answer.citations] == [True]
    assert answer.reservations == [CHANGED_NOTE, NOT_REVIEWED_NOTE]
    assert reviewer.requests == []


@pytest.mark.anyio
async def test_amendments_that_cannot_be_read_give_a_note_and_no_new_attempt() -> None:
    reviewer = ScriptedReviewer()
    graph, model = build(
        [final_answer("Tre månader [1].", [GOOD], call_id="c1")],
        amendments=DictAmendments({(SHA, 41): None}),
        reviewer=reviewer,
    )

    result = await graph.ainvoke(QUESTION, THREAD)

    answer = result["answer"]
    assert answer.status == "with_reservation"
    assert answer.reservations == ["Det gick inte att kontrollera om källa [1] har ändrats."]
    assert len(reviewer.requests) == 1  # the review still ran
    assert len(model.calls) == 1


@pytest.mark.anyio
async def test_the_amendments_of_a_failed_citation_are_not_read() -> None:
    amendments = DictAmendments({(SHA, 41): [CHANGED]})
    graph, _ = build(
        [final_answer("Tre månader [1].", [BAD], call_id="c1")], amendments=amendments, retries=0
    )

    result = await graph.ainvoke(QUESTION, THREAD)

    assert result["answer"].reservations == [
        "Källa [1] kunde inte kontrolleras mot avtalstexten.",
        NOT_REVIEWED_NOTE,
    ]
    assert amendments.reads == []


# --- ask_user and the conversation --------------------------------------------------


@pytest.mark.anyio
async def test_ask_user_pauses_the_run_and_the_answer_resumes_it() -> None:
    options = ["IT-drift Större", "IT-drift Mindre"]
    graph, model = build(
        [
            tool_call("ask_user", {"question": "Vilket avtal menar du?", "options": options}, "c1"),
            final_answer("Tre månader [1].", [GOOD], call_id="c2"),
        ]
    )

    await graph.ainvoke(QUESTION, THREAD)
    paused = await graph.aget_state(THREAD)
    resumed_nodes = [
        node
        async for update in graph.astream(
            Command(resume="IT-drift Större"), THREAD, stream_mode="updates"
        )
        for node in update
    ]

    [question] = paused.interrupts
    assert question.value == {"question": "Vilket avtal menar du?", "options": options}
    assert paused.next == ("tools",)
    state = (await graph.aget_state(THREAD)).values
    [reply] = tool_messages(state["messages"], "ask_user")
    assert reply.content == "IT-drift Större"
    assert state["answer"].status == "verified"
    # A resumed run goes on with the same question: the answer is not reset again.
    assert "AnswerCheck.before_agent" not in resumed_nodes
    assert model.calls[1][-1] == reply


@pytest.mark.anyio
@pytest.mark.parametrize(
    "options",
    [None, ["IT-drift Större"], ["IT-drift Större", " "], [f"Avtal {n}" for n in range(6)]],
    ids=["none", "one", "an empty one", "six"],
)
async def test_ask_user_without_two_to_five_options_is_refused_and_the_model_asks_again(
    options: list[str] | None,
) -> None:
    args: dict[str, Any] = {"question": "Vilket avtal menar du?"}
    if options is not None:
        args["options"] = options
    graph, model = build(
        [tool_call("ask_user", args, "c1"), tool_call("ask_user", ASK_WHICH, "c2")]
    )

    await graph.ainvoke(QUESTION, THREAD)

    # The model read why as the call's result, and its next call asked with options.
    [refused, *_] = tool_messages(model.calls[1], "ask_user")
    assert refused.status == "error"
    assert "options" in str(refused.content)
    [question] = (await graph.aget_state(THREAD)).interrupts
    assert question.value == ASK_WHICH


@pytest.mark.anyio
async def test_a_cancelled_question_tells_the_model_the_user_did_not_answer() -> None:
    graph, model = build(
        [
            tool_call("ask_user", ASK_WHICH, "c1"),
            final_answer("Det beror på avtalet.", answered=False, call_id="c2"),
        ]
    )

    await graph.ainvoke(QUESTION, THREAD)
    # ag-ui-langgraph's resume value for an AG-UI resume with status "cancelled".
    await graph.ainvoke(Command(resume={CANCELLED_MARK: True, "interrupt_id": "i1"}), THREAD)

    state = (await graph.aget_state(THREAD)).values
    [reply] = tool_messages(state["messages"], "ask_user")
    assert reply.content == NOT_ANSWERED
    assert model.calls[1][-1] == reply
    assert state["answer"].status == "no_answer"


def test_the_cancel_mark_is_ag_ui_langgraphs() -> None:
    assert CANCELLED_MARK == DEFAULT_RESUME_SENTINEL_CANCELLED


@pytest.mark.anyio
async def test_each_new_question_starts_without_the_last_answer() -> None:
    # The first question uses its new attempt, so a count kept between questions would show.
    graph, _ = build(
        [
            final_answer("Tre månader [1].", [BAD], call_id="c1"),
            final_answer("Tre månader [1].", [GOOD], call_id="c2"),
            tool_call("ask_user", ASK_WHICH, "c3"),
        ]
    )

    first = await graph.ainvoke(QUESTION, THREAD)
    assert (await graph.aget_state(THREAD)).values["validation_retries"] == 1
    await graph.ainvoke(ask("Och för Mindre?"), THREAD)

    assert first["answer"].status == "verified"
    paused = (await graph.aget_state(THREAD)).values
    assert paused["answer"] is None
    assert paused["validation_retries"] == 0
    assert sum(isinstance(m, HumanMessage) for m in paused["messages"]) == 2


@tool
def failing_search(query: str) -> str:
    """Sök i dokumenten."""
    raise ConnectionError("avtal-mcp went away")


@pytest.mark.anyio
async def test_a_new_question_never_gets_the_last_questions_failed_draft() -> None:
    # The first question's run breaks after its feedback, so its fallback stays in the state.
    graph, _ = build(
        [
            final_answer("Tre månader [1].", [BAD], call_id="c1"),
            tool_call("failing_search", {"query": "uppsägning"}, "c2"),
            *[tool_call("search_documents", {"query": "mindre"}, f"c{n}") for n in (3, 4)],
        ],
        tools=[search_documents, failing_search],
        limit=2,
    )

    with pytest.raises(ConnectionError):
        await graph.ainvoke(QUESTION, THREAD)
    assert (await graph.aget_state(THREAD)).values["fallback_answer"] is not None
    result = await graph.ainvoke(ask("Och för Mindre?"), THREAD)

    assert result["answer"] == Answer(text=NO_DRAFT_TEXT, status="no_answer", citations=[])


# --- tool errors --------------------------------------------------------------------


class ErrorSession:
    """An MCP client session whose every tool call answers with an error result."""

    def __init__(self, message: str) -> None:
        self.message = message

    async def call_tool(
        self, name: str, arguments: dict[str, Any], **kwargs: Any
    ) -> CallToolResult:
        return CallToolResult(content=[TextContent(type="text", text=self.message)], isError=True)


def mcp_tool(*, handle_tool_errors: bool) -> BaseTool:
    """avtal-mcp's read_section as the adapter makes it, on a session that answers errors."""
    schema = {
        "type": "object",
        "properties": {"sha256": {"type": "string"}, "section_number": {"type": "string"}},
        "required": ["sha256"],
    }
    return convert_mcp_tool_to_langchain_tool(
        cast(ClientSession, ErrorSession(NOT_FOUND)),
        MCPTool(name="read_section", description="Läs ett avsnitt.", inputSchema=schema),
        handle_tool_errors=handle_tool_errors,
    )


def read_section_call(call_id: str) -> AIMessage:
    return tool_call("read_section", {"sha256": SHA, "section_number": "99.9"}, call_id)


@pytest.mark.parametrize("handle_tool_errors", [True, False], ids=["adapter", "middleware"])
@pytest.mark.anyio
async def test_an_mcp_error_result_reaches_the_model_and_the_run_goes_on(
    handle_tool_errors: bool,
) -> None:
    # The adapter turns the error result into an error ToolMessage by default; with its
    # handling off it raises a ToolException, which the graph's middleware turns into one.
    graph, model = build(
        [
            read_section_call("c1"),
            final_answer("Tre månader [1].", [GOOD], call_id="c2"),
        ],
        tools=[mcp_tool(handle_tool_errors=handle_tool_errors)],
    )

    result = await graph.ainvoke(QUESTION, THREAD)

    [error] = tool_messages(result["messages"], "read_section")
    assert error.status == "error"
    assert NOT_FOUND in error.text
    assert model.calls[1][-1] == error
    assert result["answer"].status == "verified"


@pytest.mark.anyio
async def test_a_tool_exception_from_any_tool_reaches_the_model() -> None:
    def fails(sha256: str) -> str:
        raise ToolException("Dokumentet hålls tillbaka i granskningen och kan inte läsas.")

    held_back = StructuredTool.from_function(fails, name="get_outline", description="Visa.")
    graph, model = build(
        [
            tool_call("get_outline", {"sha256": SHA}, "c1"),
            final_answer("Det framgår inte.", answered=False, call_id="c2"),
        ],
        tools=[held_back],
    )

    result = await graph.ainvoke(QUESTION, THREAD)

    [error] = tool_messages(result["messages"], "get_outline")
    assert (error.status, error.content) == (
        "error",
        "Dokumentet hålls tillbaka i granskningen och kan inte läsas.",
    )
    assert result["answer"].status == "no_answer"


@pytest.mark.anyio
async def test_any_other_tool_failure_ends_the_run() -> None:
    def broken(query: str) -> str:
        raise ConnectionError("the MCP session is closed")

    graph, _ = build(
        [tool_call("search_documents", {"query": "uppsägning"}, "c1")],
        tools=[StructuredTool.from_function(broken, name="search_documents", description="Sök.")],
    )

    with pytest.raises(ConnectionError):
        await graph.ainvoke(QUESTION, THREAD)


def test_register_facts_holds_every_agreement_of_the_largest_pilot_area() -> None:
    # IT-konsulttjänster Resurskonsulter has 44 agreements in the register.
    numbers = [f"23.3-9999-2024-{n:03d}" for n in range(1, 45)]

    draft = FinalAnswer(answered=True, text="Alla 44.", citations=[], register_facts=numbers)

    assert len(draft.register_facts) == 44
    with pytest.raises(ValidationError, match="register_facts"):
        FinalAnswer(answered=True, text="Alla.", citations=[], register_facts=numbers * 2)
