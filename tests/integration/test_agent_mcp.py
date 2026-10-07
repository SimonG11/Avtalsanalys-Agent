"""Integration test: the agent with avtal-mcp's real tools on the test corpus.

What:
    Runs the agent's graph with the scripted model of the unit tests and the
    real M6 server, through the MCP SDK's in-memory client and
    `mcp_tools.load_tools`, on the corpus of `test_index_store.py`. The
    model searches, reads the general terms' 6.21.4 with `read_section` and
    quotes it in `FinalAnswer`: the answer is verified, and each field of its
    citation is the database's. A quote from a section the ingestion holds
    back is refused by `read_section` and cannot be checked: the answer is
    given with reservation.

Why:
    The unit tests check the graph with a dict for the sections and the MCP
    client with stand-in tools. Only here does the citation check read a
    section through avtal-mcp from Postgres, with its visibility rule and
    its citation fields, the way the command line and the API will.

How:
    Uses the `engine` fixture of conftest.py, and `store_corpus`,
    `build_index` and `TopicEmbedder` of `test_index_store.py`. The server
    reads through a read-only engine, as in production. The model's script
    is fixed, so the test needs no key: what it shows is what the graph,
    the tools and the check do with the calls.
"""

import json
from collections.abc import Iterator
from typing import Any

import pytest
from langchain.agents.middleware import InputAgentState
from langchain_core.messages import BaseMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session
from sqlalchemy import Engine

from avtalsagent.agent.checkpointer import serializer
from avtalsagent.agent.graph import build_agent
from avtalsagent.agent.mcp_tools import load_tools
from avtalsagent.agent.schemas import Answer, Citation
from avtalsagent.config import Settings
from avtalsagent.db import models
from avtalsagent.db.session import create_db_engine, session_factory
from avtalsagent.mcp_server.results import section_refs
from avtalsagent.mcp_server.server import build_server
from tests.integration.test_index_store import (
    HELD,
    PROCUREMENT,
    TERMS,
    TopicEmbedder,
    build_index,
    store_corpus,
)
from tests.unit.agent.scripted_model import ScriptedModel, final_answer, tool_call

# 6.21.4 of the general terms (TERMS, section 1), word for word.
PENALTY_QUOTE = "Vite ska högst uppgå till 100 000 SEK."
# 6.21.2.4, printed in the general terms and in the procurement document, whose copy is held back.
TERMINATION_QUOTE = "har Avropsberättigad rätt att skriftligen säga upp Kontraktet"
THREAD: RunnableConfig = {"configurable": {"thread_id": "integration"}}
QUESTION: InputAgentState = {
    "messages": [{"role": "user", "content": "Hur stort kan vitet bli i IT-drift Mindre?"}]
}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def indexed(engine: Engine) -> Engine:
    """The corpus as `process` stores it, with its search index."""
    store_corpus(engine)
    build_index(engine, TopicEmbedder())
    return engine


@pytest.fixture
def server(indexed: Engine) -> Iterator[FastMCP]:
    """avtal-mcp on a read-only engine of the test database, as in production."""
    read_only = create_db_engine(indexed.url.render_as_string(hide_password=False), read_only=True)
    yield build_server(session_factory(read_only), TopicEmbedder(), allowed_hosts=["localhost:*"])
    read_only.dispose()


def stored_text(engine: Engine, sha256: str, position: int) -> str:
    with session_factory(engine)() as session:
        section = session.get(models.DocumentSection, (sha256, position))
        assert section is not None
        return section.text


def tool_results(messages: list[BaseMessage], name: str) -> list[ToolMessage]:
    return [m for m in messages if isinstance(m, ToolMessage) and m.name == name]


async def run_agent(server: FastMCP, model: ScriptedModel, *, retries: int = 1) -> dict[str, Any]:
    """The question through the graph, with avtal-mcp's tools on an in-memory session."""
    settings = Settings(_env_file=None, citation_retries=retries)
    async with create_connected_server_and_client_session(server) as session:
        mcp = await load_tools(session)
        graph = build_agent(
            model, mcp.tools, mcp.reader, InMemorySaver(serde=serializer()), settings
        )
        return await graph.ainvoke(QUESTION, THREAD)


@pytest.mark.anyio
async def test_a_quote_read_through_avtal_mcp_is_verified_with_the_databases_fields(
    indexed: Engine, server: FastMCP
) -> None:
    text = stored_text(indexed, TERMS, 1)
    assert PENALTY_QUOTE in text  # the script quotes the stored text
    model = ScriptedModel(
        script=[
            tool_call(
                "search_documents",
                {"query": "Hur stort är vitet vid avtalsbrott?", "limit": 3},
                "c1",
            ),
            tool_call("read_section", {"sha256": TERMS, "section_number": "6.21.4"}, "c2"),
            final_answer(
                "Vitet är högst 100 000 SEK [1].",
                [{"id": 1, "sha256": TERMS, "section_position": 1, "quote": PENALTY_QUOTE}],
                call_id="c3",
            ),
        ]
    )

    result = await run_agent(server, model)

    with session_factory(indexed)() as session:
        ref = section_refs(session, [(TERMS, 1)])[(TERMS, 1)]
    answer = result["answer"]
    assert isinstance(answer, Answer)
    assert answer.status == "verified"
    assert answer.citations == [
        Citation(
            id=1,
            sha256=TERMS,
            file_title=ref.file_title,
            page_title=ref.page_titles[0],
            section_number=ref.section_number,
            section_title=ref.section_title,
            page=ref.page_start,
            quote=PENALTY_QUOTE,
            verified=True,
        )
    ]
    assert (ref.file_title, ref.page_titles, ref.section_number, ref.page_start) == (
        "Allmänna villkor",
        ["IT-drift Mindre, upp till 200 anställda"],
        "6.21.4",
        24,
    )
    # The model got the real tools' results: the search's hits and the whole stored section.
    messages = result["messages"]
    [search] = tool_results(messages, "search_documents")
    [read] = tool_results(messages, "read_section")
    assert search.status == read.status == "success"
    hits = json.loads(search.text)["hits"]
    assert (hits[0]["sha256"], hits[0]["section_number"]) == (TERMS, "6.21.4")
    assert json.loads(read.text)["text"] == text
    assert model.calls[2][-1] == read


@pytest.mark.anyio
async def test_a_quote_from_a_held_back_section_cannot_be_checked(
    indexed: Engine, server: FastMCP
) -> None:
    assert TERMINATION_QUOTE in stored_text(indexed, PROCUREMENT, HELD.section_position)
    model = ScriptedModel(
        script=[
            tool_call(
                "read_section",
                {"sha256": PROCUREMENT, "section_position": HELD.section_position},
                "c1",
            ),
            final_answer(
                "Vid väsentligt avtalsbrott får avropsberättigad säga upp kontraktet [1].",
                [
                    {
                        "id": 1,
                        "sha256": PROCUREMENT,
                        "section_position": HELD.section_position,
                        "quote": TERMINATION_QUOTE,
                    }
                ],
                call_id="c2",
            ),
        ]
    )

    result = await run_agent(server, model, retries=0)

    messages = result["messages"]
    [refused] = tool_results(messages, "read_section")
    assert refused.status == "error"
    assert "hålls tillbaka i granskningen" in refused.text
    answer = result["answer"]
    assert isinstance(answer, Answer)
    assert answer.status == "with_reservation"
    [citation] = answer.citations
    assert (citation.sha256, citation.file_title, citation.verified) == (PROCUREMENT, "", False)
