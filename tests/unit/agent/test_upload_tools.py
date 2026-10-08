"""Tests for avtalsagent.agent.list_uploads, .read_upload and .thread_files: the file tools.

The tools run against the memory store with a contract read by the API's
parser (uploaded.py), called as the graph calls them, with the run's config.
Each conversation sees only its own files; an unknown, malformed or other
conversation's id is "not found"; a query ranks the sections by their words
and returns the best whole, within the limits; a store that does not answer
is a Swedish error for the model.
"""

import json
from typing import Any

import pytest
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, ToolException

from avtalsagent.agent import list_uploads as list_module
from avtalsagent.agent import read_upload as read_module
from avtalsagent.agent.list_uploads import make_list_uploads
from avtalsagent.agent.read_upload import MAX_MATCHES, make_read_upload, rank_sections, words
from avtalsagent.agent.thread_files import NO_THREAD, STORE_DOWN, current_thread, thread_of
from avtalsagent.domain.uploads import UploadSection
from avtalsagent.uploads.store import UploadStore
from tests.unit.agent.uploaded import LIABILITY, DownStore, add, memory_store

T1: RunnableConfig = {"configurable": {"thread_id": "t1"}}
T2: RunnableConfig = {"configurable": {"thread_id": "t2"}}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def call(tool: BaseTool, args: dict[str, Any], config: RunnableConfig = T1) -> Any:
    return json.loads(await tool.ainvoke(args, config=config))


def section(position: int, title: str, text: str) -> UploadSection:
    return UploadSection(
        position=position, number=str(position), title=title, level=1, page_start=None, text=text
    )


# --- list_uploads -----------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_uploads_gives_the_threads_files_with_their_outline() -> None:
    store = memory_store()
    upload = await add(store, "t1")
    await add(store, "t2", "annat.md", b"# 1 Annat\nNagot annat.\n")

    listed = await call(make_list_uploads(store), {})

    [file] = listed["uploads"]
    assert file["upload_id"] == str(upload.upload_id)
    assert (file["filename"], file["kind"], file["pages"]) == ("avtal.md", "text", None)
    assert file["sha256"] == upload.sha256
    assert file["sections_total"] == 6
    assert file["sections"][LIABILITY] == {
        "section_position": LIABILITY,
        "section_number": "4",
        "section_title": "Skadestånd och ansvarsbegränsning",
        "level": 1,
        "page_start": None,
    }


@pytest.mark.anyio
async def test_a_thread_without_files_lists_none() -> None:
    store = memory_store()
    await add(store, "t1")

    assert await call(make_list_uploads(store), {}, T2) == {"uploads": []}


@pytest.mark.anyio
async def test_the_outline_is_cut_but_says_how_many_sections_there_are(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(list_module, "MAX_OUTLINE", 2)
    store = memory_store()
    await add(store, "t1")

    [file] = (await call(make_list_uploads(store), {}))["uploads"]

    assert [s["section_position"] for s in file["sections"]] == [0, 1]
    assert file["sections_total"] == 6


# --- read_upload: one section -----------------------------------------------------------


@pytest.mark.anyio
async def test_a_section_has_the_citation_fields_of_read_section() -> None:
    store = memory_store()
    upload = await add(store, "t1")

    read = await call(
        make_read_upload(store),
        {"upload_id": str(upload.upload_id), "section_position": LIABILITY},
    )

    assert read == {
        "source": "upload",
        "upload_id": str(upload.upload_id),
        "filename": "avtal.md",
        "sha256": upload.sha256,
        "section_position": LIABILITY,
        "section_number": "4",
        "section_title": "Skadestånd och ansvarsbegränsning",
        "page_start": None,
        "text": "4. Skadestånd och ansvarsbegränsning\n\nLeverantörens totala skadeståndsansvar "
        "är begränsat till 10 procent av Kontraktets värde.",
    }


@pytest.mark.anyio
async def test_another_threads_file_is_not_found() -> None:
    store = memory_store()
    upload = await add(store, "t1")

    with pytest.raises(ToolException, match="Det finns ingen fil med upload_id"):
        await make_read_upload(store).ainvoke(
            {"upload_id": str(upload.upload_id), "section_position": 1}, config=T2
        )


@pytest.mark.parametrize("upload_id", ["inte-ett-id", "00000000-0000-0000-0000-000000000000"])
@pytest.mark.anyio
async def test_a_malformed_or_unknown_id_is_not_found(upload_id: str) -> None:
    store = memory_store()
    await add(store, "t1")

    with pytest.raises(ToolException, match="list_uploads"):
        await make_read_upload(store).ainvoke(
            {"upload_id": upload_id, "section_position": 1}, config=T1
        )


@pytest.mark.anyio
async def test_a_position_the_file_does_not_have_names_the_positions_it_has() -> None:
    store = memory_store()
    upload = await add(store, "t1")

    with pytest.raises(ToolException, match="inget avsnitt på plats 99; den har platserna 0–5"):
        await make_read_upload(store).ainvoke(
            {"upload_id": str(upload.upload_id), "section_position": 99}, config=T1
        )


@pytest.mark.parametrize(
    "args",
    [{}, {"section_position": 1, "query": "ansvar"}, {"query": "  "}],
    ids=["neither", "both", "blank-query"],
)
@pytest.mark.anyio
async def test_exactly_one_of_position_and_query_is_given(args: dict[str, Any]) -> None:
    store = memory_store()
    upload = await add(store, "t1")

    with pytest.raises(ToolException, match="antingen section_position"):
        await make_read_upload(store).ainvoke(
            {"upload_id": str(upload.upload_id), **args}, config=T1
        )


# --- read_upload: a query ---------------------------------------------------------------


@pytest.mark.anyio
async def test_a_query_returns_the_best_sections_whole_and_names_the_next() -> None:
    store = memory_store()
    upload = await add(store, "t1")

    found = await call(
        make_read_upload(store),
        {"upload_id": str(upload.upload_id), "query": "Vad gäller för ansvarsbegränsningen?"},
    )

    assert found["query"] == "Vad gäller för ansvarsbegränsningen?"
    assert [s["section_position"] for s in found["sections"]] == [LIABILITY]
    assert found["sections"][0]["text"].endswith("10 procent av Kontraktets värde.")
    assert found["other_matches"] == []


@pytest.mark.anyio
async def test_a_query_no_section_matches_gives_no_sections() -> None:
    store = memory_store()
    upload = await add(store, "t1")

    found = await call(
        make_read_upload(store), {"upload_id": str(upload.upload_id), "query": "sekretess"}
    )

    assert (found["sections"], found["other_matches"]) == ([], [])


@pytest.mark.anyio
async def test_a_query_of_stopwords_only_is_refused() -> None:
    store = memory_store()
    upload = await add(store, "t1")

    with pytest.raises(ToolException, match="query har inga ord"):
        await make_read_upload(store).ainvoke(
            {"upload_id": str(upload.upload_id), "query": "vad är det som gäller?"}, config=T1
        )


@pytest.mark.anyio
async def test_a_query_returns_at_most_the_sections_that_fit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(read_module, "MAX_MATCH_CHARS", 250)
    kinds = ["försening", "fel", "faktura", "utbyte", "avtalsbrott", "sekretess"]
    clauses = "".join(
        f"## {n}. Vite vid {kind}\n{'Vite utgår. ' * (n + 4)}\n\n"
        for n, kind in enumerate(kinds, start=1)
    )
    store = memory_store()
    upload = await add(store, "t1", "viten.md", clauses.encode())

    found = await call(
        make_read_upload(store), {"upload_id": str(upload.upload_id), "query": "vite"}
    )

    sizes = [len(s["text"]) for s in found["sections"]]
    assert 1 <= len(sizes) <= MAX_MATCHES
    assert sum(sizes) <= 250 or len(sizes) == 1  # the best always comes, whole
    returned = {s["section_position"] for s in found["sections"]}
    assert returned.isdisjoint(found["other_matches"])
    assert len(returned) + len(found["other_matches"]) == 6


def test_query_words_are_folded_without_stopwords_and_short_words() -> None:
    assert words("Vad gäller för ANSVAR, vite och 10 % i 2.19.8?") == ["ansvar", "vite"]


def test_a_query_word_finds_compounds_and_inflections_by_its_first_six_letters() -> None:
    sections = [
        section(0, "Parter", "Kunden och leverantören."),
        section(1, "Ansvar", "Ansvaret är begränsat."),
        section(2, "Vite", "Ansvarsbegränsningen gäller inte vite."),
    ]

    ranked = rank_sections(sections, "ansvarsbegränsning")

    # The query's word is the stem "ansvar", which both have; a title's word counts twice.
    assert [s.position for s in ranked] == [1, 2]


def test_rarer_words_weigh_more_and_ties_keep_the_files_order() -> None:
    sections = [
        section(0, "A", "avtal uppsägning"),
        section(1, "B", "avtal"),
        section(2, "C", "avtal"),
        section(3, "D", "avtal uppsägning"),
    ]

    ranked = rank_sections(sections, "avtal uppsägning")

    assert [s.position for s in ranked] == [0, 3, 1, 2]


# --- the thread and the store -----------------------------------------------------------


@pytest.mark.parametrize("make", [make_list_uploads, make_read_upload])
@pytest.mark.anyio
async def test_a_store_that_does_not_answer_is_an_error_for_the_model(
    make: Any, caplog: pytest.LogCaptureFixture
) -> None:
    store: UploadStore = DownStore()
    upload_id = "00000000-0000-0000-0000-000000000001"
    args = {} if make is make_list_uploads else {"upload_id": upload_id, "section_position": 0}

    with pytest.raises(ToolException) as raised:
        await make(store).ainvoke(args, config=T1)

    assert str(raised.value) == STORE_DOWN
    assert "connection refused" in caplog.text  # logged, not shown to the model


@pytest.mark.anyio
async def test_a_run_without_a_thread_reads_no_files() -> None:
    store = memory_store()
    await add(store, "t1")

    with pytest.raises(ToolException) as raised:
        await make_list_uploads(store).ainvoke({}, config={})

    assert str(raised.value) == NO_THREAD


def test_the_thread_is_the_configs_and_none_outside_a_run() -> None:
    assert thread_of(T1) == "t1"
    assert thread_of({}) is None
    assert thread_of(None) is None
    assert current_thread() is None


@pytest.mark.parametrize(
    ("make", "arguments"),
    [(make_list_uploads, set()), (make_read_upload, {"upload_id", "section_position", "query"})],
)
def test_the_model_gives_no_thread_and_no_config(make: Any, arguments: set[str]) -> None:
    assert set(make(memory_store()).args) == arguments
