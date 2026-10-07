"""Tests for the user's files in the agent's runs over AG-UI, through the whole app.

A file uploaded with `POST /api/uploads` in a thread is what the agent of
that thread reads: its tools are steps like the others, and the answer's
citation of the file reaches the web app in the STATE_SNAPSHOT with
`source` "upload", the upload's id and the file's name (webbapp-kontrakt.md,
point 37). A thread without files runs as before: no upload tools, no
section in the prompt. The app runs with the memory store, a scripted model
and no database.
"""

import hashlib

import pytest

from avtalsagent.agent.upload_prompt import UPLOADS_PROMPT
from tests.unit.agent.scripted_model import final_answer, tool_call
from tests.unit.agent.uploaded import CONTRACT, LIABILITY, LIABILITY_QUOTE
from tests.unit.api.test_api_agui import QUESTION, app_with, client_of, of_type, post, run_input

SHA = hashlib.sha256(CONTRACT).hexdigest()  # the citation's sha256 is the file's
COMPARE = [{"id": "u1", "role": "user", "content": "Jämför mitt avtal med ramavtalet."}]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_the_answer_cites_the_threads_file_as_an_upload() -> None:
    app, model = app_with([])

    async with client_of(app) as client:
        uploaded = await client.post(
            "/api/uploads",
            files={"file": ("avtal.md", CONTRACT, "text/markdown")},
            data={"thread_id": "t1"},
        )
        assert uploaded.status_code == 201
        upload_id = uploaded.json()["upload_id"]
        model.script += [
            tool_call("list_uploads", {}, "c1"),
            tool_call("read_upload", {"upload_id": upload_id, "section_position": LIABILITY}, "c2"),
            final_answer(
                "Ditt avtal begränsar ansvaret till 10 procent [1].",
                [
                    {
                        "id": 1,
                        "sha256": SHA,
                        "section_position": LIABILITY,
                        "quote": LIABILITY_QUOTE,
                    }
                ],
                call_id="c3",
            ),
        ]
        events = await post(client, run_input("r1", COMPARE))

    started = [event["toolCallName"] for event in of_type(events, "TOOL_CALL_START")]
    assert started[:2] == ["list_uploads", "read_upload"]
    answer = of_type(events, "STATE_SNAPSHOT")[-1]["snapshot"]["answer"]
    assert answer["status"] == "verified"
    assert answer["citations"] == [
        {
            "id": 1,
            "sha256": SHA,
            "file_title": "avtal.md",
            "page_title": None,
            "section_number": "4",
            "section_title": "Skadestånd och ansvarsbegränsning",
            "page": None,
            "quote": LIABILITY_QUOTE,
            "verified": True,
            "source": "upload",
            "upload_id": upload_id,
        }
    ]
    assert model.bound[0] == [
        "search_documents",
        "ask_user",
        "list_uploads",
        "read_upload",
        "FinalAnswer",
    ]
    assert f"upload_id {upload_id}" in model.calls[0][0].text


@pytest.mark.anyio
async def test_a_thread_without_files_runs_as_before() -> None:
    app, model = app_with([final_answer("Det framgår inte.", answered=False, call_id="c1")])

    async with client_of(app) as client:
        uploaded = await client.post(
            "/api/uploads",
            files={"file": ("avtal.md", CONTRACT, "text/markdown")},
            data={"thread_id": "another-thread"},
        )
        assert uploaded.status_code == 201
        events = await post(client, run_input("r1", QUESTION))

    assert of_type(events, "STATE_SNAPSHOT")[-1]["snapshot"]["answer"]["status"] == "no_answer"
    assert model.bound == [["search_documents", "ask_user", "FinalAnswer"]]
    assert UPLOADS_PROMPT.splitlines()[0] not in model.calls[0][0].text
