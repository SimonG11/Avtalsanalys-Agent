"""list_uploads: the files the user uploaded in the conversation, each with its outline.

What:
    `make_list_uploads(store)` gives the agent's tool `list_uploads`, which
    returns the conversation's uploads (`UploadList`): each file's
    upload_id, name, kind, pages, sha256, warnings and its sections'
    outline, as avtal-mcp's `get_outline` gives a document's.

Why:
    The agent compares the user's own file with the framework agreements
    (ADR 0026), and starts, as with an agreement, from what the file holds:
    which sections it has and what they are called, so it reads the ones
    the question is about with `read_upload`. The tool lives in the graph,
    not in avtal-mcp, which is read-only over the shared corpus and never
    sees a user's file. The model learns of the files in every model call
    (`upload_prompt.py`); this tool gives the outline that the prompt
    leaves out.

How:
    The thread comes from the run's config (`thread_files.py`), never from
    the model. The outline lists at most `MAX_OUTLINE` sections per file,
    in order, and says how many there are; the rest are found with
    `read_upload`'s query. A store that does not answer is a
    `ToolException` with Swedish text, which `ToolErrorMiddleware` hands
    the model as the tool's result.
"""

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel

from avtalsagent.agent.thread_files import require_thread, stored
from avtalsagent.uploads.store import UploadStore

LIST_UPLOADS = "list_uploads"
# Sections listed per file; a file has at most about 1.5 million characters, so a few hundred.
MAX_OUTLINE = 200

DESCRIPTION = """\
Lista filerna som användaren har laddat upp i det här samtalet: upload_id, filnamn, filtyp, \
sidor och filens avsnitt (section_position, section_number, section_title, page_start).

Filerna är användarens egna dokument, till exempel ett avropsavtal att jämföra med \
ramavtalen. De är inte ramavtal och finns bara i det här samtalet. Läs ett avsnitt med \
read_upload."""


class UploadOutlineSection(BaseModel):
    section_position: int  # 0-based place in the file
    section_number: str | None  # None for a section without a number
    section_title: str
    level: int  # 1 for "6", 2 for "6.2"; 0 for the text before the first heading
    page_start: int | None  # 1-based PDF page; None for Word and text


class UploadOverview(BaseModel):
    upload_id: str
    filename: str
    kind: str  # "pdf", "docx" or "text"
    pages: int | None  # PDF pages; None for Word and text
    sha256: str  # of the file's bytes, as its sections' citations have it
    warnings: list[str]  # e.g. pages without text, which the agent does not see
    sections_total: int
    sections: list[UploadOutlineSection]  # the first MAX_OUTLINE, in order


class UploadList(BaseModel):
    uploads: list[UploadOverview]  # oldest first; empty when the user has uploaded none


class _NoArguments(BaseModel):
    pass


def make_list_uploads(store: UploadStore) -> BaseTool:
    """The tool `list_uploads` over `store`."""

    async def list_uploads(config: RunnableConfig) -> str:
        thread = require_thread(config)
        overviews = []
        for upload in await stored(store.list_uploads(thread)):
            sections = await stored(store.read_sections(thread, upload.upload_id))
            overviews.append(
                UploadOverview(
                    upload_id=str(upload.upload_id),
                    filename=upload.filename,
                    kind=upload.kind.value,
                    pages=upload.pages,
                    sha256=upload.sha256,
                    warnings=list(upload.warnings),
                    sections_total=len(sections),
                    sections=[
                        UploadOutlineSection(
                            section_position=section.position,
                            section_number=section.number,
                            section_title=section.title,
                            level=section.level,
                            page_start=section.page_start,
                        )
                        for section in sections[:MAX_OUTLINE]
                    ],
                )
            )
        return UploadList(uploads=overviews).model_dump_json()

    return StructuredTool.from_function(
        coroutine=list_uploads,
        name=LIST_UPLOADS,
        description=DESCRIPTION,
        args_schema=_NoArguments,
    )
