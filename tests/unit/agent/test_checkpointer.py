"""Tests for avtalsagent.agent.checkpointer: the agent's classes survive a checkpoint.

Each round trip goes through a checkpointer's own put and get, or through a
small compiled graph, both with LANGGRAPH_STRICT_MSGPACK unset and set.
LangGraph reads the variable once, at import, into module constants; the
tests set those constants as the import would. The Postgres branch is
checked for its address, its pool and its one `setup()` with a stand-in
pool and saver: no test opens a database connection.
"""

from typing import Any, TypedDict

import langgraph._internal._serde as graph_serde
import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver, ChannelVersions, empty_checkpoint
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde import _msgpack
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from psycopg import conninfo
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel
from sqlalchemy.engine import make_url

from avtalsagent.agent import checkpointer
from avtalsagent.agent.checkpointer import (
    CHECKPOINT_TYPES,
    POOL_SIZE,
    checkpoint_dsn,
    open_checkpointer,
    serializer,
)
from avtalsagent.agent.schemas import Answer, AvtalState, Citation, DraftCitation, FinalAnswer
from avtalsagent.config import Settings

SHA = "ab" * 32
QUOTE = "en uppsägningstid om tre (3) månader"
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


class Stranger(BaseModel):
    """A class the allowlist does not name."""

    text: str


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(params=[False, True], ids=["default", "LANGGRAPH_STRICT_MSGPACK=true"])
def strict(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> bool:
    # What `import langgraph` sets from the variable: the serializer's default and whether
    # compile() adds the state's classes to the checkpointer's allowlist.
    monkeypatch.setattr(_msgpack, "STRICT_MSGPACK_ENABLED", request.param)
    monkeypatch.setattr(graph_serde, "STRICT_MSGPACK_ENABLED", request.param)
    flag: bool = request.param
    return flag


def memory_settings() -> Settings:
    return Settings(_env_file=None, checkpointer="memory")


async def round_trip(saver: BaseCheckpointSaver[str], values: dict[str, Any]) -> dict[str, Any]:
    """Save `values` as a checkpoint's channels with the saver, and read them back."""
    config: RunnableConfig = {"configurable": {"thread_id": "t1", "checkpoint_ns": ""}}
    checkpoint = empty_checkpoint()
    versions: ChannelVersions = {name: saver.get_next_version(None, None) for name in values}
    checkpoint["channel_values"] = values
    checkpoint["channel_versions"] = versions
    saved = await saver.aput(config, checkpoint, {}, versions)
    loaded = await saver.aget_tuple(saved)
    assert loaded is not None
    return loaded.checkpoint["channel_values"]


# --- the serializer ---


@pytest.mark.anyio
async def test_the_draft_and_the_answer_come_back_as_their_classes(strict: bool) -> None:
    async with open_checkpointer(memory_settings()) as saver:
        values = await round_trip(saver, {"structured_response": DRAFT, "answer": ANSWER})

    assert type(values["structured_response"]) is FinalAnswer
    assert values["structured_response"] == DRAFT
    assert type(values["structured_response"].citations[0]) is DraftCitation
    assert type(values["answer"]) is Answer
    assert values["answer"] == ANSWER
    assert type(values["answer"].citations[0]) is Citation


@pytest.mark.anyio
async def test_without_the_allowlist_strict_mode_gives_the_draft_back_as_a_dict() -> None:
    # The failure the allowlist prevents: the check would read `draft.answered` from a dict.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(_msgpack, "STRICT_MSGPACK_ENABLED", True)
        values = await round_trip(InMemorySaver(serde=JsonPlusSerializer()), {"draft": DRAFT})

    assert values["draft"] == DRAFT.model_dump()


@pytest.mark.anyio
async def test_a_class_the_allowlist_does_not_name_stays_a_dict(strict: bool) -> None:
    # A checkpoint cannot make the process build, or import, a class it does not expect.
    values = await round_trip(InMemorySaver(serde=serializer()), {"other": Stranger(text="x")})

    assert values["other"] == {"text": "x"}


class Draft(TypedDict, total=False):
    question: str
    structured_response: FinalAnswer | None
    answer: Answer | None


async def hand_in(state: Draft) -> Draft:
    return {"structured_response": DRAFT, "answer": ANSWER}


@pytest.mark.anyio
async def test_a_graphs_state_comes_back_as_the_classes(strict: bool) -> None:
    builder = StateGraph(Draft)
    builder.add_node("hand_in", hand_in)
    builder.add_edge(START, "hand_in")
    builder.add_edge("hand_in", END)
    config: RunnableConfig = {"configurable": {"thread_id": "t1"}}

    async with open_checkpointer(memory_settings()) as saver:
        graph = builder.compile(checkpointer=saver)
        await graph.ainvoke({"question": "Hur lång är uppsägningstiden?"}, config)
        state = await graph.aget_state(config)

    assert type(state.values["structured_response"]) is FinalAnswer
    assert type(state.values["answer"]) is Answer
    assert state.values["answer"] == ANSWER


def our_classes(annotation: Any, found: set[type]) -> set[type]:
    """Every Pydantic class of avtalsagent in a type annotation, and in those classes' fields."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        if annotation.__module__.startswith("avtalsagent.") and annotation not in found:
            found.add(annotation)
            for field in annotation.model_fields.values():
                our_classes(field.annotation, found)
        return found
    for argument in getattr(annotation, "__args__", ()):
        our_classes(argument, found)
    return found


def test_the_allowlist_names_every_class_of_ours_in_the_agents_state() -> None:
    # A class of ours added to the state later must be added to CHECKPOINT_TYPES too. The
    # draft is the type argument of create_agent's state (AgentState[FinalAnswer]).
    found: set[type] = set()
    for annotation in [*AvtalState.__annotations__.values(), FinalAnswer]:
        our_classes(annotation, found)

    assert found == set(CHECKPOINT_TYPES)


# --- which checkpointer ---


@pytest.mark.anyio
async def test_memory_is_an_in_memory_saver_with_the_serializer() -> None:
    async with open_checkpointer(memory_settings()) as saver:
        assert isinstance(saver, InMemorySaver)
        assert isinstance(saver.serde, JsonPlusSerializer)


class StandInPool:
    """Stands in for AsyncConnectionPool: records how it was made, opens no connection."""

    made: list["StandInPool"] = []
    check_connection = AsyncConnectionPool.check_connection

    def __init__(self, conninfo: str, **options: Any) -> None:
        self.conninfo = conninfo
        self.options = options
        self.open = False
        self.closed = False
        StandInPool.made.append(self)

    async def __aenter__(self) -> "StandInPool":
        self.open = True
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.closed = True


class StandInSaver:
    """Stands in for AsyncPostgresSaver: records its pool and serializer, counts setup()."""

    def __init__(self, conn: StandInPool, *, serde: JsonPlusSerializer) -> None:
        self.conn = conn
        self.serde = serde
        self.setups = 0

    async def setup(self) -> None:
        assert self.conn.open, "setup() before the pool was opened"
        self.setups += 1


@pytest.mark.anyio
async def test_postgres_opens_a_pool_on_database_url_without_the_driver_and_sets_up_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    StandInPool.made = []
    monkeypatch.setattr(checkpointer, "AsyncConnectionPool", StandInPool)
    monkeypatch.setattr(checkpointer, "AsyncPostgresSaver", StandInSaver)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@db:5432/avtalsagent")
    settings = Settings(_env_file=None, checkpointer="postgres")

    async with open_checkpointer(settings) as yielded:
        assert isinstance(yielded, StandInSaver)
        assert yielded.setups == 1
        (pool,) = StandInPool.made
        assert yielded.conn is pool
        assert not pool.closed

    assert pool.closed
    assert pool.conninfo == "postgresql://u:p@db:5432/avtalsagent"
    # Opened by the block, with POOL_SIZE connections, each checked before it is lent.
    assert pool.options["open"] is False
    assert pool.options["max_size"] == POOL_SIZE
    assert pool.options["check"] is AsyncConnectionPool.check_connection
    # What the saver needs of a connection, as AsyncPostgresSaver.from_conn_string sets it.
    assert pool.options["kwargs"] == {
        "autocommit": True,
        "prepare_threshold": 0,
        "row_factory": dict_row,
    }
    # The agent's serializer, by what it does: the agent's classes back, any other a dict.
    serde = yielded.serde
    assert type(serde.loads_typed(serde.dumps_typed(DRAFT))) is FinalAnswer
    assert serde.loads_typed(serde.dumps_typed(Stranger(text="x"))) == {"text": "x"}


@pytest.mark.parametrize(
    ("url", "dsn"),
    [
        ("postgresql+psycopg://u:p@db:5432/x", "postgresql://u:p@db:5432/x"),
        ("postgresql://u:p@db:5432/x", "postgresql://u:p@db:5432/x"),
        ("postgresql+psycopg://u:a+psycopg@db/x", "postgresql://u:a+psycopg@db/x"),
        (
            "postgresql+psycopg://u:p@db/x?sslmode=require",
            "postgresql://u:p@db/x?sslmode=require",
        ),
    ],
    ids=["with the driver", "without it", "a password holding the driver's name", "a query"],
)
def test_the_dsn_is_database_url_without_sqlalchemys_driver(url: str, dsn: str) -> None:
    assert checkpoint_dsn(url) == dsn


@pytest.mark.parametrize(
    "password",
    ["Sommar50%off!", "Hemligt%Losen99", "p%40ss%3Aw%2Frd", "a+psycopg", "avtalsagent"],
    ids=["a bare %", "% before letters", "percent-encoded", "the driver's name", "plain"],
)
def test_libpq_reads_the_password_sqlalchemy_reads(password: str) -> None:
    url = f"postgresql+psycopg://u:{password}@db:5432/x"

    parsed = conninfo.conninfo_to_dict(checkpoint_dsn(url))

    assert parsed["password"] == make_url(url).password
    assert (parsed["host"], parsed["port"], parsed["dbname"]) == ("db", "5432", "x")


def test_the_checkpointer_is_memory_or_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHECKPOINTER", raising=False)
    assert Settings(_env_file=None).checkpointer == "memory"
    monkeypatch.setenv("CHECKPOINTER", "postgres")
    assert Settings(_env_file=None).checkpointer == "postgres"
    monkeypatch.setenv("CHECKPOINTER", "sqlite")
    with pytest.raises(ValueError, match="checkpointer"):
        Settings(_env_file=None)
