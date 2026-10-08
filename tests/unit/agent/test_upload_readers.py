"""Tests for avtalsagent.agent.upload_readers, and the check of a citation of the user's file.

The readers find a cited hash among the conversation's files and read the
section from the store, else ask the fallback (avtal-mcp's stand-in); a file
has no amendments, and a store that stops answering at the section gives
none, without asking the fallback. Through the whole graph, an answer that
cites the user's file is verified with the citation's fields from the
store, a forged quote fails and goes back to the model naming read_upload,
a hash from another conversation is not found, a date in the file backs the
same date in the answer, and the reviewer is told which source is the
user's file.
"""

from datetime import date, timedelta
from typing import Any
from uuid import UUID

import pytest
from langchain.agents.middleware import InputAgentState
from langchain_core.messages import ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver

from avtalsagent.agent.amendments import AmendmentInfo
from avtalsagent.agent.checkpointer import serializer
from avtalsagent.agent.graph import build_agent
from avtalsagent.agent.mcp_tools import McpTools
from avtalsagent.agent.reviewer import UPLOAD_NOTE, review_messages
from avtalsagent.agent.schemas import Answer
from avtalsagent.agent.upload_readers import UploadAmendmentReader, UploadSectionReader
from avtalsagent.config import Settings
from avtalsagent.domain.uploads import Upload, UploadSection
from avtalsagent.uploads.store import MemoryUploadStore, UploadStoreUnavailable
from avtalsagent.validation.review import ReviewInput, ReviewSource
from tests.unit.agent.scripted_model import (
    DictAmendments,
    DictReader,
    ListRegister,
    ScriptedModel,
    ScriptedReviewer,
    final_answer,
    tool_call,
)
from tests.unit.agent.test_agent_graph import GOOD, SECTION, SHA, search_documents
from tests.unit.agent.uploaded import (
    LIABILITY,
    LIABILITY_QUOTE,
    DownStore,
    add,
    memory_store,
)
from tests.unit.uploads.upload_files import AGREEMENT_PAGES, pdf_bytes

T1: RunnableConfig = {"configurable": {"thread_id": "t1"}}
CHANGE = AmendmentInfo(
    sha256="c3" * 32,
    section_position=2,
    file_title="Ändring 1",
    section_number="1",
    section_title="Ändring",
    amended_position=41,
    status="resolved",
    dated=date(2026, 1, 1),
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def in_thread(thread: str | None) -> Any:
    return lambda: thread


# --- the readers ------------------------------------------------------------------------


@pytest.mark.anyio
async def test_a_hash_of_the_conversations_file_is_read_from_the_store() -> None:
    store = memory_store()
    upload = await add(store, "t1")
    fallback = DictReader([SECTION])

    section = await UploadSectionReader(store, fallback, in_thread("t1")).read(
        upload.sha256, LIABILITY
    )

    assert section is not None
    assert (section.source, section.upload_id) == ("upload", str(upload.upload_id))
    assert (section.sha256, section.section_position) == (upload.sha256, LIABILITY)
    assert (section.file_title, section.page_titles, section.page_start) == ("avtal.md", [], None)
    assert (section.section_number, section.section_title) == (
        "4",
        "Skadestånd och ansvarsbegränsning",
    )
    assert LIABILITY_QUOTE in section.text
    assert fallback.reads == []


@pytest.mark.anyio
async def test_a_pdfs_section_has_its_page() -> None:
    store = memory_store()
    upload = await add(store, "t1", "drift.pdf", pdf_bytes(AGREEMENT_PAGES))
    sections = await store.read_sections("t1", upload.upload_id)
    last = sections[-1]

    section = await UploadSectionReader(store, DictReader([]), in_thread("t1")).read(
        upload.sha256, last.position
    )

    assert section is not None
    assert section.page_start == last.page_start == 4


@pytest.mark.anyio
async def test_another_hash_is_avtal_mcps() -> None:
    store = memory_store()
    await add(store, "t1")
    fallback = DictReader([SECTION])

    section = await UploadSectionReader(store, fallback, in_thread("t1")).read(SHA, 41)

    assert section == SECTION
    assert section.source == "framework"
    assert fallback.reads == [(SHA, 41)]


@pytest.mark.parametrize("thread", ["t2", None], ids=["another-thread", "no-thread"])
@pytest.mark.anyio
async def test_another_conversations_file_is_not_found(thread: str | None) -> None:
    store = memory_store()
    upload = await add(store, "t1")
    fallback = DictReader([SECTION])  # avtal-mcp does not know the file either

    reader = UploadSectionReader(store, fallback, in_thread(thread))

    assert await reader.read(upload.sha256, LIABILITY) is None
    assert fallback.reads == [(upload.sha256, LIABILITY)]


@pytest.mark.anyio
async def test_a_position_the_file_does_not_have_is_none_without_asking_avtal_mcp() -> None:
    store = memory_store()
    upload = await add(store, "t1")
    fallback = DictReader([SECTION])

    assert (
        await UploadSectionReader(store, fallback, in_thread("t1")).read(upload.sha256, 99) is None
    )
    assert fallback.reads == []


class DownAtTheSection(MemoryUploadStore):
    """A store that lists the thread's files but does not answer when a section is read."""

    async def read_section(
        self, thread_id: str, upload_id: UUID, position: int
    ) -> UploadSection | None:
        raise UploadStoreUnavailable("connection refused")


@pytest.mark.anyio
async def test_a_store_that_stops_answering_at_the_section_gives_none_without_avtal_mcp(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = DownAtTheSection(timedelta(days=7))
    upload = await add(store, "t1")
    fallback = DictReader([SECTION])

    reader = UploadSectionReader(store, fallback, in_thread("t1"))

    assert await reader.read(upload.sha256, LIABILITY) is None
    assert fallback.reads == []
    assert "connection refused" in caplog.text


@pytest.mark.anyio
async def test_a_store_that_does_not_answer_leaves_the_reading_to_avtal_mcp() -> None:
    fallback = DictReader([SECTION])

    assert (
        await UploadSectionReader(DownStore(), fallback, in_thread("t1")).read(SHA, 41) == SECTION
    )


@pytest.mark.anyio
async def test_the_conversations_file_has_no_amendments_and_others_are_avtal_mcps() -> None:
    store = memory_store()
    upload = await add(store, "t1")
    fallback = DictAmendments({(SHA, 41): [CHANGE]})
    reader = UploadAmendmentReader(store, fallback, in_thread("t1"))

    assert await reader.read(upload.sha256, LIABILITY) == []
    assert await reader.read(SHA, 41) == [CHANGE]
    assert fallback.reads == [(SHA, 41)]


# --- the check, through the graph -------------------------------------------------------


async def ask(
    store: MemoryUploadStore,
    script: list[Any],
    *,
    reviewer: ScriptedReviewer | None = None,
    amendments: DictAmendments | None = None,
    retries: int = 0,
) -> dict[str, Any]:
    model = ScriptedModel(script=script)
    mcp = McpTools(
        tools=[search_documents],
        reader=DictReader([SECTION]),
        register=ListRegister(),
        amendments=amendments or DictAmendments(),
    )
    graph = build_agent(
        model,
        mcp,
        reviewer or ScriptedReviewer(),
        InMemorySaver(serde=serializer()),
        Settings(_env_file=None, validation_retries=retries),
        today=lambda: date(2026, 10, 7),
        uploads=store,
    )
    question: InputAgentState = {
        "messages": [{"role": "user", "content": "Jämför mitt avtal med ramavtalet."}]
    }
    result: dict[str, Any] = await graph.ainvoke(question, T1)
    return result


def cites(
    upload: Upload, quote: str = LIABILITY_QUOTE, position: int = LIABILITY
) -> dict[str, Any]:
    return {"id": 1, "sha256": upload.sha256, "section_position": position, "quote": quote}


@pytest.mark.anyio
async def test_a_quote_from_the_users_file_is_verified_with_the_files_fields() -> None:
    store = memory_store()
    upload = await add(store, "t1")
    reviewer = ScriptedReviewer()
    amendments = DictAmendments()
    framework = {**GOOD, "id": 2}

    result = await ask(
        store,
        [
            tool_call("read_upload", {"upload_id": str(upload.upload_id), "query": "ansvar"}, "c1"),
            final_answer(
                "Ditt avtal begränsar ansvaret till 10 procent [1]; ramavtalet har tre "
                "månaders uppsägningstid [2].",
                [cites(upload), framework],
                call_id="c2",
            ),
        ],
        reviewer=reviewer,
        amendments=amendments,
    )

    answer = result["answer"]
    assert isinstance(answer, Answer)
    assert answer.status == "verified"
    upload_citation, framework_citation = answer.citations
    assert upload_citation.model_dump() == {
        "id": 1,
        "sha256": upload.sha256,
        "file_title": "avtal.md",
        "page_title": None,
        "section_number": "4",
        "section_title": "Skadestånd och ansvarsbegränsning",
        "page": None,
        "quote": LIABILITY_QUOTE,
        "verified": True,
        "source": "upload",
        "upload_id": str(upload.upload_id),
    }
    assert (framework_citation.source, framework_citation.upload_id) == ("framework", None)
    assert amendments.reads == [(SHA, 41)]  # not the user's file: avtal-mcp does not know it
    [review] = reviewer.requests
    assert [source.source for source in review.sources] == ["upload", "framework"]


@pytest.mark.anyio
async def test_a_forged_quote_from_the_users_file_fails() -> None:
    store = memory_store()
    upload = await add(store, "t1")

    result = await ask(
        store,
        [
            final_answer(
                "Ditt avtal begränsar ansvaret till 50 procent [1].",
                [cites(upload, "skadeståndsansvar är begränsat till 50 procent")],
                call_id="c1",
            )
        ],
    )

    answer = result["answer"]
    assert answer.status == "with_reservation"
    [citation] = answer.citations
    assert (citation.verified, citation.source) == (False, "upload")


@pytest.mark.anyio
async def test_a_forged_quote_from_the_users_file_goes_back_naming_read_upload() -> None:
    store = memory_store()
    upload = await add(store, "t1")

    result = await ask(
        store,
        [
            final_answer(
                "Ditt avtal begränsar ansvaret till 50 procent [1].",
                [cites(upload, "skadeståndsansvar är begränsat till 50 procent")],
                call_id="c1",
            ),
            final_answer("Det framgår inte.", answered=False, call_id="c2"),
        ],
        retries=1,
    )

    feedback = [m for m in result["messages"] if isinstance(m, ToolMessage) and m.status == "error"]
    assert "Kopiera det ur texten från read_upload," in feedback[0].text
    assert "från read_section, utan" not in feedback[0].text


@pytest.mark.anyio
async def test_a_hash_from_another_conversations_file_fails_and_goes_back_to_the_model() -> None:
    store = memory_store()
    elsewhere = await add(store, "t2")

    result = await ask(
        store,
        [
            final_answer("Ansvaret är begränsat [1].", [cites(elsewhere)], call_id="c1"),
            final_answer("Det framgår inte.", answered=False, call_id="c2"),
        ],
        retries=1,
    )

    feedback = [m for m in result["messages"] if isinstance(m, ToolMessage) and m.status == "error"]
    assert "det finns inget avsnitt att läsa" in feedback[0].text
    assert result["answer"].status == "no_answer"


@pytest.mark.anyio
async def test_a_date_in_the_users_file_backs_the_same_date_in_the_answer() -> None:
    store = memory_store()
    upload = await add(store, "t1")

    result = await ask(
        store,
        [
            final_answer(
                "Ditt kontrakt gäller till och med 2027-09-30 [1].",
                [cites(upload, "till och med 2027-09-30", position=2)],
                call_id="c1",
            )
        ],
    )

    assert result["answer"].status == "verified"


# --- the reviewer -----------------------------------------------------------------------


def test_the_reviewer_is_told_which_source_is_the_users_file() -> None:
    def source(n: int, kind: Any) -> ReviewSource:
        return ReviewSource(
            id=n,
            file_title="avtal.md" if kind == "upload" else "Allmänna villkor",
            section_number="4",
            section_title="Ansvar",
            text="Texten.",
            source=kind,
        )

    request = ReviewInput(
        question="Jämför.",
        follow_ups=[],
        answer_text="Svaret [1][2].",
        sources=[source(1, "upload"), source(2, "framework")],
        register_facts=[],
        today=date(2026, 10, 7),
    )

    data = review_messages(request)[1].text

    assert data.count(UPLOAD_NOTE) == 1
    assert f"Dokument: avtal.md\n{UPLOAD_NOTE}\nAvsnitt: 4 Ansvar" in data
    assert "Dokument: Allmänna villkor\nAvsnitt: 4 Ansvar" in data
