"""Integration test: the agent's checkpoints in Postgres (agent/checkpointer.py).

What:
    Saves the agent's draft and answer with `AsyncPostgresSaver`, opened by
    `open_checkpointer` with CHECKPOINTER=postgres, and reads them back as
    their classes from a checkpointer opened again, as after a restart,
    whose `setup()` finds its tables already there. Then runs the graph to
    an `ask_user` question, opens everything again and resumes the run with
    the user's answer: the question waited in Postgres, and the answer is
    verified.

Why:
    The API (M9) keeps its conversations in Postgres, so a run that waits
    for the user survives a restart, and the state holds the agent's own
    Pydantic classes, which LangGraph's serializer gives back only when they
    are allowed. psycopg also takes another URL than SQLAlchemy's, which
    only a real connection shows.

How:
    Uses the Postgres container of conftest.py, with DATABASE_URL in
    SQLAlchemy's form ("+psycopg"), as the settings have it. The saver gets
    a database of its own on that server, dropped after each test: it
    creates its own tables, and `test_migrations.py` compares the main test
    database with the models. The model is the scripted one of the unit
    tests and the cited section comes from a dict, so no key is needed.
"""

from collections.abc import Iterator
from datetime import date
from typing import Any

import pytest
from langchain.agents.middleware import InputAgentState
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver, ChannelVersions, empty_checkpoint
from langgraph.types import Command
from sqlalchemy import Engine, text

from avtalsagent.agent.checkpointer import open_checkpointer
from avtalsagent.agent.graph import build_agent
from avtalsagent.agent.mcp_tools import McpTools
from avtalsagent.agent.schemas import Answer, Citation, DraftCitation, FinalAnswer, RegisterFact
from avtalsagent.agent.sections import CitedSection
from avtalsagent.config import Settings
from tests.unit.agent.scripted_model import (
    DictReader,
    ListRegister,
    ScriptedModel,
    ScriptedReviewer,
    final_answer,
    tool_call,
)

DATABASE = "agent_checkpoints"
SHA = "ab" * 32
QUOTE = "en uppsägningstid om tre (3) månader"
SECTION = CitedSection(
    sha256=SHA,
    section_position=42,
    section_number="6.21.9",
    section_title="Uppsägning",
    file_title="Allmänna villkor",
    page_titles=["IT-drift Större, fler än 200 anställda"],
    page_start=14,
    text=f"Kontraktet kan sägas upp av Kunden med {QUOTE}.",
)
DRAFT = FinalAnswer(
    answered=True,
    text="Uppsägningstiden är tre månader [1].",
    citations=[DraftCitation(id=1, sha256=SHA, section_position=42, quote=QUOTE)],
)
ANSWER = Answer(
    text=DRAFT.text,
    status="verified",
    citations=[
        Citation(
            id=1,
            sha256=SHA,
            file_title="Allmänna villkor",
            page_title="IT-drift Större, fler än 200 anställda",
            section_number="6.21.9",
            section_title="Uppsägning",
            page=14,
            quote=QUOTE,
            verified=True,
        )
    ],
)
# An answer with reservation from the register, as the check gives it after the retries.
RESERVED = Answer(
    text="Avtalet gäller till 2028-11-14 enligt registret.",
    status="with_reservation",
    citations=[],
    reservations=["Kunde inte kontrolleras mot registret: 2028-11-14."],
    register_facts=[
        RegisterFact(
            agreement_number="23.3-5890-2023-002",
            supplier_name="Nordlo Advance AB",
            org_number="556486-1689",
            sub_area="IT-drift Mindre",
            valid_from=date(2024, 11, 14),
            valid_to=date(2028, 11, 13),
            max_extension_to=None,
        )
    ],
)
CONFIG: RunnableConfig = {"configurable": {"thread_id": "integration", "checkpoint_ns": ""}}
THREAD: RunnableConfig = {"configurable": {"thread_id": "conversation"}}


def mcp() -> McpTools:
    return McpTools(tools=[], reader=DictReader([SECTION]), register=ListRegister())


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def settings(engine: Engine) -> Iterator[Settings]:
    """CHECKPOINTER=postgres on a database of its own, in SQLAlchemy's URL form."""
    with engine.connect() as connection:
        connection.execution_options(isolation_level="AUTOCOMMIT")  # no transaction around it
        connection.execute(text(f"DROP DATABASE IF EXISTS {DATABASE} WITH (FORCE)"))
        connection.execute(text(f"CREATE DATABASE {DATABASE}"))
    url = engine.url.set(database=DATABASE).render_as_string(hide_password=False)
    assert url.startswith("postgresql+psycopg://")
    yield Settings(_env_file=None, checkpointer="postgres", database_url=url)
    with engine.connect() as connection:
        connection.execution_options(isolation_level="AUTOCOMMIT")
        connection.execute(text(f"DROP DATABASE {DATABASE} WITH (FORCE)"))


async def save(saver: BaseCheckpointSaver[str], values: dict[str, Any]) -> RunnableConfig:
    checkpoint = empty_checkpoint()
    versions: ChannelVersions = {name: saver.get_next_version(None, None) for name in values}
    checkpoint["channel_values"] = values
    checkpoint["channel_versions"] = versions
    return await saver.aput(CONFIG, checkpoint, {}, versions)


@pytest.mark.anyio
async def test_the_draft_and_the_answer_survive_postgres_and_a_restart(settings: Settings) -> None:
    async with open_checkpointer(settings) as saver:
        saved = await save(
            saver, {"structured_response": DRAFT, "answer": ANSWER, "fallback_answer": RESERVED}
        )
    async with open_checkpointer(settings) as saver:  # setup() again: the tables are there
        loaded = await saver.aget_tuple(saved)

    assert loaded is not None
    values = loaded.checkpoint["channel_values"]
    assert type(values["structured_response"]) is FinalAnswer
    assert values["structured_response"] == DRAFT
    assert type(values["answer"]) is Answer
    assert values["answer"] == ANSWER
    assert values["fallback_answer"] == RESERVED
    assert type(values["fallback_answer"].register_facts[0]) is RegisterFact


@pytest.mark.anyio
async def test_a_run_waiting_for_the_user_is_resumed_after_a_restart(settings: Settings) -> None:
    question: InputAgentState = {
        "messages": [{"role": "user", "content": "Vilken uppsägningstid gäller?"}]
    }
    options = ["IT-drift Större", "IT-drift Mindre"]
    asking = ScriptedModel(
        script=[tool_call("ask_user", {"question": "Vilket avtal?", "options": options}, "c1")]
    )
    answering = ScriptedModel(
        script=[final_answer(DRAFT.text, [DRAFT.citations[0].model_dump()], call_id="c2")]
    )

    async with open_checkpointer(settings) as saver:
        graph = build_agent(asking, mcp(), ScriptedReviewer(), saver, settings)
        await graph.ainvoke(question, THREAD)
    async with open_checkpointer(settings) as saver:  # a new process: the graph built again
        graph = build_agent(answering, mcp(), ScriptedReviewer(), saver, settings)
        waiting = await graph.aget_state(THREAD)
        await graph.ainvoke(Command(resume="IT-drift Större"), THREAD)
        state = (await graph.aget_state(THREAD)).values

    [interrupt] = waiting.interrupts
    assert interrupt.value == {"question": "Vilket avtal?", "options": options}
    assert type(state["structured_response"]) is FinalAnswer
    assert type(state["answer"]) is Answer
    assert state["answer"] == ANSWER
    # The resumed model saw the user's answer as ask_user's result.
    assert answering.calls[0][-1].content == "IT-drift Större"
