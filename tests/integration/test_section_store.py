"""Integration test: the section tables (ingestion/section_store.py) against a real Postgres.

What:
    Loads the sample register and a catalog with one document, then checks
    the files and links that steps 2 and 3 read, and that saving sections
    twice replaces them.

Why:
    The joins from a file to its pages and to the register, and the
    replacement of all sections in one run, are SQL that unit tests cannot
    cover.

How:
    Uses the `engine` fixture from conftest.py. The document is split by
    step 3 itself, so the stored rows are exactly what a run would store.
"""

from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import Engine, delete, func, select

from avtalsagent.db import models
from avtalsagent.db.session import session_factory
from avtalsagent.domain.documents import AgreementPage, DocumentLink
from avtalsagent.domain.parsed import Block, BlockKind, ParsedDocument
from avtalsagent.ingestion.catalog import save_fetch
from avtalsagent.ingestion.section_store import (
    document_links,
    load_outline,
    save_sections,
    source_files,
)
from avtalsagent.ingestion.step1_fetch import FetchResult, FetchStatus, StoredDocument
from avtalsagent.ingestion.step3_chunk import chunk_document, document_context
from avtalsagent.register.load import load_register
from avtalsagent.register.normalize import normalize_rows
from avtalsagent.register.read_excel import read_register

SHA = "d" * 64
PAGE = "https://www.avropa.se/ramavtal/ramavtalsomraden/x/bemanningstjanster/"
DOC = "https://www.avropa.se/globalassets/bilagor/ramavtalets-huvuddokument.pdf"


@pytest.fixture
def catalog(engine: Engine, sample_register_xlsx: Path) -> Engine:
    raw = read_register(sample_register_xlsx)
    link = DocumentLink(
        url=DOC,
        version="v1",
        title="Ramavtalets huvuddokument",
        category="Avtal",
        agreement_number=None,
        file_type="pdf",
        site_updated=date(2026, 8, 27),
    )
    page = AgreementPage(
        url=PAGE,
        title="Bemanningstjänster - IT-tjänster upp till 1000 timmar",
        procurement_numbers=("23.3-14537-2023",),
        agreement_period=None,
        documents=(link,),
    )
    stored = StoredDocument(DOC, "v1", SHA, 123, f"documents/{SHA}.pdf")
    with session_factory(engine).begin() as session:
        session.execute(delete(models.ParsedFile))
        for model in (models.AgreementPageDocument, models.SourceDocument, models.AgreementPage):
            session.execute(delete(model))
        load_register(session, raw.version, normalize_rows(raw.rows).rows)
        save_fetch(session, [page], [FetchResult(link, FetchStatus.NEW, stored)], [PAGE])
    return engine


def parsed(*headings: str) -> ParsedDocument:
    blocks: list[Block] = []
    for heading in headings:
        blocks.append(Block(kind=BlockKind.HEADING, text=heading, page=1))
        blocks.append(Block(kind=BlockKind.TEXT, text=f"Text under {heading}.", page=1))
    return ParsedDocument(
        sha256=SHA, file_type="pdf", parser="test", pages=(), blocks=tuple(blocks)
    )


def test_files_and_links_for_steps_2_and_3(catalog: Engine) -> None:
    with session_factory(catalog)() as session:
        files = source_files(session, Path("data"))
        links = document_links(session)
    assert [(f.sha256, f.file_type, f.path) for f in files] == [
        (SHA, "pdf", Path(f"data/documents/{SHA}.pdf"))
    ]
    context = document_context(links[SHA])
    assert context.agreement == "Bemanningstjänster (23.3-14537-2023)"
    assert context.document == "Ramavtalets huvuddokument"


def test_saving_sections_twice_replaces_them(catalog: Engine) -> None:
    factory = session_factory(catalog)
    with factory() as session:
        context = document_context(document_links(session)[SHA])
    first = parsed("1 Inledning", "2 Avtalets omfattning", "2.1 Delområden")
    second = parsed("1 Inledning", "2 Avtalets omfattning")
    with factory.begin() as session:
        save_sections(session, [first], [chunk_document(first, context)])
    with factory.begin() as session:
        save_sections(session, [second], [chunk_document(second, context)])

    with factory() as session:
        sections = load_outline(session, SHA[:12])
        chunk_headers = session.scalars(
            select(models.SectionChunk.context_header).order_by(
                models.SectionChunk.section_position
            )
        ).all()
        files = session.scalars(select(models.ParsedFile)).all()
        assert session.scalar(select(func.count()).select_from(models.DocumentSection)) == 2

    assert [(s.number, s.title, s.level, s.path) for s in sections] == [
        ("1", "Inledning", 1, ["1 Inledning"]),
        ("2", "Avtalets omfattning", 1, ["2 Avtalets omfattning"]),
    ]
    assert chunk_headers == [
        "Bemanningstjänster (23.3-14537-2023) › Ramavtalets huvuddokument › 1 Inledning",
        "Bemanningstjänster (23.3-14537-2023) › Ramavtalets huvuddokument › 2 Avtalets omfattning",
    ]
    assert [(f.outline, f.section_count, f.chunk_count) for f in files] == [("numbered", 2, 2)]
