"""Integration test: the document catalog (ingestion/catalog.py) against a real Postgres.

What:
    Loads the sample register, then saves fetch results twice and checks the
    rows in agreement_page, source_document and agreement_page_document.

Why:
    The upserts and the replacement of a page's links are SQL that unit tests
    cannot cover. The second save must update rows, never duplicate them.

How:
    Uses the `engine` fixture from conftest.py. Each test starts by clearing
    the document tables, so the tests do not depend on each other.
"""

from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import Engine, delete, select

from avtalsagent.db import models
from avtalsagent.db.session import session_factory
from avtalsagent.domain.documents import AgreementPage, DocumentLink
from avtalsagent.ingestion.catalog import (
    UnknownAreaError,
    load_stored_documents,
    procurements_for_areas,
    save_fetch,
)
from avtalsagent.ingestion.step1_fetch import FetchResult, FetchStatus, StoredDocument
from avtalsagent.register.load import load_register
from avtalsagent.register.normalize import normalize_rows
from avtalsagent.register.read_excel import read_register

PAGE = "https://www.avropa.se/ramavtal/ramavtalsomraden/x/bemanningstjanster/"
DOC = "https://www.avropa.se/globalassets/bilagor/ramavtalets-huvuddokument.pdf"


def link(url: str = DOC, title: str = "Ramavtalets huvuddokument") -> DocumentLink:
    return DocumentLink(
        url=url,
        version="v1",
        title=title,
        category="Avtal",
        agreement_number=None,
        file_type="pdf",
        site_updated=date(2026, 8, 27),
    )


def page(*links: DocumentLink) -> AgreementPage:
    return AgreementPage(
        url=PAGE,
        title="Bemanningstjänster",
        procurement_numbers=("23.3-14537-2023",),
        agreement_period="2025-04-03 - 2029-04-02",
        documents=links,
    )


def result(document_link: DocumentLink, sha256: str = "a" * 64) -> FetchResult:
    stored = StoredDocument(document_link.url, "v1", sha256, 123, f"documents/{sha256}.pdf")
    return FetchResult(document_link, FetchStatus.NEW, stored)


@pytest.fixture
def empty_catalog(engine: Engine) -> Engine:
    with session_factory(engine).begin() as session:
        for model in (models.AgreementPageDocument, models.SourceDocument, models.AgreementPage):
            session.execute(delete(model))
    return engine


def test_saving_twice_updates_instead_of_duplicating(empty_catalog: Engine) -> None:
    factory = session_factory(empty_catalog)
    with factory.begin() as session:
        save_fetch(session, [page(link())], [result(link())])
    with factory.begin() as session:
        save_fetch(session, [page(link())], [result(link(), sha256="b" * 64)])

    with factory() as session:
        stored = load_stored_documents(session)
        links = session.scalars(select(models.AgreementPageDocument)).all()

    assert list(stored) == [DOC]
    assert stored[DOC].sha256 == "b" * 64
    assert [(row.page_url, row.document_url, row.category) for row in links] == [
        (PAGE, DOC, "Avtal")
    ]


def test_link_removed_from_page_is_removed_from_catalog(empty_catalog: Engine) -> None:
    other = link(f"{DOC}.old", "Gammal bilaga")
    factory = session_factory(empty_catalog)
    with factory.begin() as session:
        save_fetch(session, [page(link(), other)], [result(link()), result(other, "c" * 64)])
    with factory.begin() as session:
        save_fetch(session, [page(link())], [result(link())])

    with factory() as session:
        linked = session.scalars(select(models.AgreementPageDocument.document_url)).all()
        documents = session.scalars(select(models.SourceDocument.url)).all()

    assert linked == [DOC]
    assert sorted(documents) == sorted([DOC, f"{DOC}.old"])  # the file record is kept


def test_failed_document_is_not_linked(empty_catalog: Engine) -> None:
    factory = session_factory(empty_catalog)
    failed = FetchResult(link(), FetchStatus.FAILED, None, "download failed")
    with factory.begin() as session:
        save_fetch(session, [page(link())], [failed])

    with factory() as session:
        assert session.scalars(select(models.AgreementPageDocument)).all() == []
        assert session.scalars(select(models.AgreementPage.url)).all() == [PAGE]


def test_areas_are_turned_into_procurement_numbers(
    engine: Engine, sample_register_xlsx: Path
) -> None:
    raw = read_register(sample_register_xlsx)
    factory = session_factory(engine)
    with factory.begin() as session:
        load_register(session, raw.version, normalize_rows(raw.rows).rows)

    with factory() as session:
        assert procurements_for_areas(session, ["Bemanningstjänster"]) == {
            "Bemanningstjänster": {"23.3-14537-2023"}
        }
        with pytest.raises(UnknownAreaError, match="IT-drift"):
            procurements_for_areas(session, ["IT-drift"])
