"""get_outline: a document's sections in order, as its table of contents.

What:
    `get_outline` returns a file's citation fields (sha256, title, type,
    agreement pages) and each of its sections: position, number, title,
    level and first page. Sections the quarantine holds back are left out
    and counted in `held_back`.

Why:
    The search finds sections by their words; the outline lets the agent
    find one by its place: the section before or after a hit, the
    subsections of a chapter, or the right number when a reference or the
    user names one that does not exist. It also gives `section_position` for
    a section without a number, which `read_section` takes. The count of
    held-back sections tells the model that a gap in the numbers is not the
    document's own.

How:
    One query for the file's fields and one for its sections, after
    `read_section.require_file` has checked the file against the visibility.
    A visible file always has its metadata and scope, since an unchecked
    file is held back and an unindexed one has no scope.
"""

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from avtalsagent.db import models
from avtalsagent.mcp_server.arguments import Sha256
from avtalsagent.mcp_server.tools.read_section import require_file
from avtalsagent.mcp_server.visibility import load_visibility


class OutlineSection(BaseModel):
    section_position: int  # 0-based place in the file
    section_number: str | None  # None for a section without a number
    section_title: str
    level: int  # 1 for "6", 3 for "6.21.9"; 0 for the text before the first heading
    page_start: int | None  # 1-based PDF page; None for Word files


class Outline(BaseModel):
    """A file and its sections in order, without the held-back ones."""

    sha256: str
    file_title: str
    document_type: str
    page_titles: list[str]
    sections: list[OutlineSection]
    held_back: int  # sections left out because the quarantine holds them


def get_outline(session: Session, sha256: Sha256) -> Outline:
    """Visa ett dokuments innehåll: alla avsnitt i ordning med nummer, rubrik, nivå och sida.

    Använd när du vet vilket dokument du vill läsa men inte vilket avsnitt, för att se
    vad som står före och efter en sökträff, eller för att kontrollera ett nummer som inte
    hittades. Texten läser du sedan med read_section, med section_number eller, för ett
    avsnitt utan nummer, section_position. held_back anger hur många avsnitt som hålls
    tillbaka i granskningen och därför saknas i listan.
    """
    visibility = load_visibility(session)
    require_file(session, visibility, sha256)
    metadata, scope = models.DocumentMetadata, models.DocumentScope
    file = session.execute(
        select(metadata.title, metadata.document_type, scope.page_titles)
        .join(scope, scope.sha256 == metadata.sha256)
        .where(metadata.sha256 == sha256)
    ).one()
    section = models.DocumentSection
    rows = session.execute(
        select(section.position, section.number, section.title, section.level, section.page_start)
        .where(section.sha256 == sha256)
        .order_by(section.position)
    ).all()
    shown = [row for row in rows if visibility.shows_section(sha256, row.position)]
    return Outline(
        sha256=sha256,
        file_title=file.title,
        document_type=file.document_type,
        page_titles=list(file.page_titles),
        sections=[
            OutlineSection(
                section_position=row.position,
                section_number=row.number,
                section_title=row.title,
                level=row.level,
                page_start=row.page_start,
            )
            for row in shown
        ],
        held_back=len(rows) - len(shown),
    )
