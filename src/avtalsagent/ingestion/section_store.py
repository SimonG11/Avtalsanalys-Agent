"""The database side of steps 2 and 3: which files to parse, and the sections.

What:
    `source_files` lists the downloaded files (one per hash) for step 2.
    `document_links` returns, per file, how avropa.se links to it, for the
    context headers. `save_sections` replaces the stored sections and chunks.
    `load_outline` reads one file's table of contents back.

Why:
    `step2_parse.py` and `step3_chunk.py` work on plain values so they can be
    tested without a database. This module is the only place that reads the
    catalog for them and writes their results, as `catalog.py` is for step 1.

How:
    SQLAlchemy statements on the tables in `db/models.py`. Sections are keyed
    by the file's hash: the same file linked from several pages is parsed and
    split once. Each run replaces all sections and chunks in one transaction,
    so files that are no longer in the catalog disappear and a reader never
    sees half a run. A file's sections and chunks are sent as a list of rows
    (executemany), which SQLAlchemy sends in batches: one statement with all
    of them would pass Postgres' limit of 65,535 parameters at 6,554 sections
    (10 parameters each). The pilot's largest file has 1,505.
"""

from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path

from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from avtalsagent.db import models
from avtalsagent.domain.parsed import ParsedDocument
from avtalsagent.ingestion.step2_parse import SourceFile
from avtalsagent.ingestion.step3_chunk import ChunkedDocument, LinkInfo


def source_files(session: Session, data_dir: Path) -> list[SourceFile]:
    """Every downloaded file once, in hash order (a file can be linked from several URLs)."""
    rows = session.execute(
        select(
            models.SourceDocument.sha256,
            models.SourceDocument.file_type,
            models.SourceDocument.local_path,
        ).order_by(models.SourceDocument.sha256)
    )
    files: dict[str, SourceFile] = {}
    for sha256, file_type, path in rows:
        files.setdefault(sha256, SourceFile(sha256, file_type, data_dir / path))
    return list(files.values())


def document_links(session: Session) -> dict[str, list[LinkInfo]]:
    """How the pages on avropa.se link to each file, with the register's names."""
    areas_by_procurement: defaultdict[str, set[str]] = defaultdict(set)
    for procurement, area in session.execute(
        select(models.Agreement.procurement_number, models.SubArea.framework_area)
        .join(
            models.AgreementSubArea,
            models.AgreementSubArea.agreement_number == models.Agreement.agreement_number,
        )
        .join(models.SubArea, models.SubArea.id == models.AgreementSubArea.sub_area_id)
        .distinct()
    ):
        areas_by_procurement[procurement].add(area)
    supplier_names = {
        number: name
        for number, name in session.execute(
            select(models.Agreement.agreement_number, models.Agreement.supplier_name)
        )
    }

    rows = session.execute(
        select(
            models.SourceDocument.sha256,
            models.AgreementPageDocument.title,
            models.AgreementPageDocument.agreement_number,
            models.AgreementPage.title,
            models.AgreementPage.procurement_numbers,
        )
        .join(
            models.AgreementPageDocument,
            models.AgreementPageDocument.document_url == models.SourceDocument.url,
        )
        .join(
            models.AgreementPage, models.AgreementPage.url == models.AgreementPageDocument.page_url
        )
    )
    links: defaultdict[str, list[LinkInfo]] = defaultdict(list)
    for sha256, title, agreement_number, page_title, procurements in rows:
        links[sha256].append(
            LinkInfo(
                title=title,
                agreement_number=agreement_number,
                supplier_name=supplier_names.get(agreement_number) if agreement_number else None,
                page_title=page_title,
                procurement_numbers=tuple(procurements),
                framework_areas=tuple(
                    sorted({area for p in procurements for area in areas_by_procurement[p]})
                ),
            )
        )
    return dict(links)


def save_sections(
    session: Session, parsed: Sequence[ParsedDocument], chunked: Sequence[ChunkedDocument]
) -> None:
    """Replace all stored sections and chunks with this run's."""
    session.execute(delete(models.ParsedFile))  # cascades to sections and chunks
    by_sha = {document.sha256: document for document in parsed}
    for document in chunked:
        source = by_sha[document.sha256]
        session.execute(
            insert(models.ParsedFile).values(
                sha256=document.sha256,
                file_type=source.file_type,
                parser=source.parser,
                page_count=len(source.pages),
                pages_needing_ocr=source.pages_needing_ocr,
                outline=document.outline.value,
                section_count=len(document.sections),
                chunk_count=len(document.chunks),
            )
        )
        if document.sections:
            session.execute(
                insert(models.DocumentSection),
                [
                    {
                        "sha256": document.sha256,
                        "position": section.position,
                        "number": section.number,
                        "title": section.title,
                        "level": section.level,
                        "parent_position": section.parent,
                        "path": list(section.path),
                        "page_start": section.page_start,
                        "page_end": section.page_end,
                        "text": section.text,
                    }
                    for section in document.sections
                ],
            )
        if document.chunks:
            session.execute(
                insert(models.SectionChunk),
                [
                    {
                        "sha256": document.sha256,
                        "section_position": chunk.section,
                        "position": chunk.position,
                        "context_header": chunk.context_header,
                        "text": chunk.text,
                    }
                    for chunk in document.chunks
                ],
            )


def load_outline(session: Session, sha256_prefix: str) -> list[models.DocumentSection]:
    """The sections of the file whose hash starts with `sha256_prefix`, in order."""
    return list(
        session.scalars(
            select(models.DocumentSection)
            .where(models.DocumentSection.sha256.startswith(sha256_prefix))
            .order_by(models.DocumentSection.sha256, models.DocumentSection.position)
        )
    )
