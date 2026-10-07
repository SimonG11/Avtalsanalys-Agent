"""Integration test: the PDF route's document lookup against Postgres (api/documents.py).

What:
    `DatabaseDocumentFiles.find` on the test corpus of `test_index_store.py`,
    through a read-only engine as in the API: an indexed file is found with
    its stored path under the data directory, also when the ingestion holds
    back one of its sections; a file the ingestion holds back as a whole,
    and a hash nobody stored, are not; and a file stored as Word comes back
    as Word, which the route answers with 404.

Why:
    The unit tests give the route a stand-in lookup. Only here does the
    lookup read `document_scope`, the findings and `source_document` with
    the visibility rule avtal-mcp uses, so the API cannot serve what the
    tools would not show.

How:
    Uses the `engine` fixture of conftest.py and `store_corpus` and
    `build_index` of `test_index_store.py`. Each test stores the corpus
    again; the rows a test adds or changes are put back afterwards, so the
    tests that follow see the corpus as stored.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, delete, func, select, update

from avtalsagent.api.documents import DatabaseDocumentFiles, StoredFile
from avtalsagent.db import models
from avtalsagent.db.session import create_db_engine, session_factory
from avtalsagent.domain.extracted import Severity
from tests.integration.test_index_store import (
    ADVANIA_CARD,
    HELD,
    PROCUREMENT,
    TERMS,
    TopicEmbedder,
    build_index,
    store_corpus,
)


@pytest.fixture
def files(engine: Engine, tmp_path: Path) -> Iterator[DatabaseDocumentFiles]:
    """The lookup on a read-only engine of the indexed corpus, with `tmp_path` as DATA_DIR."""
    store_corpus(engine)
    build_index(engine, TopicEmbedder())
    read_only = create_db_engine(engine.url.render_as_string(hide_password=False), read_only=True)
    yield DatabaseDocumentFiles(session_factory(read_only), tmp_path)
    read_only.dispose()


def test_an_indexed_file_is_found_at_its_stored_path(
    files: DatabaseDocumentFiles, tmp_path: Path
) -> None:
    assert files.find(TERMS) == StoredFile(
        file_type="pdf", path=tmp_path / "documents" / f"{TERMS}.pdf"
    )


def test_a_file_with_a_held_back_section_is_still_found(files: DatabaseDocumentFiles) -> None:
    # The quarantine holds back one section of it; the tools show the rest, and so does the PDF.
    assert HELD.sha256 == PROCUREMENT

    found = files.find(PROCUREMENT)

    assert found is not None
    assert found.file_type == "pdf"


def test_a_hash_nobody_stored_is_not_found(files: DatabaseDocumentFiles) -> None:
    assert files.find("0" * 64) is None


def test_a_file_held_back_as_a_whole_is_not_found(
    files: DatabaseDocumentFiles, engine: Engine
) -> None:
    factory = session_factory(engine)
    with factory.begin() as session:
        number = session.scalar(select(func.coalesce(func.max(models.ValidationFinding.id), 0)))
        assert number is not None
        session.add(
            models.ValidationFinding(
                id=number + 1,
                check_name="org_numbers",
                severity=Severity.QUARANTINE.value,
                subject="556000-0000",
                message="Kortet nämner en annan leverantör än registret.",
                sha256=ADVANIA_CARD,
                section_position=None,
            )
        )
    try:
        assert files.find(ADVANIA_CARD) is None
    finally:
        with factory.begin() as session:
            session.execute(
                delete(models.ValidationFinding).where(models.ValidationFinding.id == number + 1)
            )


def test_a_word_file_comes_back_as_word(files: DatabaseDocumentFiles, engine: Engine) -> None:
    factory = session_factory(engine)
    word = models.SourceDocument.sha256 == TERMS
    with factory.begin() as session:
        session.execute(update(models.SourceDocument).where(word).values(file_type="docx"))
    try:
        found = files.find(TERMS)
    finally:
        with factory.begin() as session:
            session.execute(update(models.SourceDocument).where(word).values(file_type="pdf"))

    assert found is not None
    assert found.file_type == "docx"
