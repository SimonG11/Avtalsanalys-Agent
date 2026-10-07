"""The fields every section a tool returns carries, and how they are read.

What:
    `SectionRef`: where a section is (file, type, pages, number, title).
    `section_refs` reads them for a set of sections in one query.

Why:
    The agent's answer cites sections with these fields (the web app's
    contract: sha256, file_title, page_title, section_number, section_title,
    page). When every tool returns them the same way, the citation step
    copies them from a tool result instead of looking them up, and the web
    app can open the PDF on the right page.

How:
    A Pydantic model, so a tool result's schema shows the fields, and one
    query joining `document_section`, `document_metadata` (the file's title
    and type) and `document_scope` (the agreement pages' titles). It returns
    only the sections it finds; visibility is the caller's
    (`visibility.py`).
"""

from collections.abc import Collection

from pydantic import BaseModel
from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from avtalsagent.db import models


class SectionRef(BaseModel):
    """Where a section is: the fields an answer's citation copies."""

    sha256: str
    file_title: str  # the link text on avropa.se, e.g. "Allmänna villkor"
    document_type: str  # `DocumentType` value, e.g. "general_terms"
    page_titles: list[str]  # the agreement pages that link to the file
    section_position: int  # 0-based place in the file; finds a section without a number
    section_number: str | None  # e.g. "6.21.9"; None for a section without one
    section_title: str
    page_start: int | None  # 1-based PDF page; None for Word files
    page_end: int | None


def section_refs(
    session: Session, keys: Collection[tuple[str, int]]
) -> dict[tuple[str, int], SectionRef]:
    """The `SectionRef` of each (sha256, section position) that exists, by key."""
    if not keys:
        return {}
    section, metadata = models.DocumentSection, models.DocumentMetadata
    scope = models.DocumentScope
    rows = session.execute(
        select(
            section.sha256,
            section.position,
            section.number,
            section.title,
            section.page_start,
            section.page_end,
            metadata.title.label("file_title"),
            metadata.document_type,
            scope.page_titles,
        )
        .join(metadata, metadata.sha256 == section.sha256)
        .join(scope, scope.sha256 == section.sha256)
        .where(tuple_(section.sha256, section.position).in_(list(keys)))
    )
    return {
        (row.sha256, row.position): SectionRef(
            sha256=row.sha256,
            file_title=row.file_title,
            document_type=row.document_type,
            page_titles=list(row.page_titles),
            section_position=row.position,
            section_number=row.number,
            section_title=row.title,
            page_start=row.page_start,
            page_end=row.page_end,
        )
        for row in rows
    }
