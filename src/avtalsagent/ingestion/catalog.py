"""The document catalog in Postgres: what step 1 has read and downloaded.

What:
    `procurements_for_areas` looks up the procurement numbers of framework
    areas in the register. `load_stored_documents` returns what earlier runs
    downloaded. `save_fetch` records the pages, the downloaded documents and
    which page links to which document, and marks the stored pages that the
    site no longer lists.

Why:
    `step1_fetch.py` works on plain values so it can be tested without a
    database. This module is the only place that turns those values into rows,
    so the database side of step 1 can be read in one file.

How:
    SQLAlchemy Core statements on the tables in `db/models.py`. Pages and
    documents are upserted (insert, or update on the same URL); a document only
    when this run downloaded it, so a failed download leaves the stored row as
    it was. The links of each page read in this run are replaced, so a link
    removed from a page disappears from the catalog while the downloaded file
    stays on disk. A page the index no longer lists is not deleted: it gets
    `missing_since`, so step 5 can hold back files that no page publishes any
    more, with the reason.
"""

from collections.abc import Collection, Iterable, Mapping, Sequence

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from avtalsagent.db import models
from avtalsagent.domain.documents import AgreementPage
from avtalsagent.ingestion.step1_fetch import FetchResult, FetchStatus, StoredDocument


class UnknownAreaError(ValueError):
    """Raised when a framework area is not in the register."""


def procurements_for_areas(session: Session, areas: Iterable[str]) -> dict[str, set[str]]:
    """Map each framework area to the procurement numbers of its agreements in the register."""
    wanted = list(areas)
    rows = session.execute(
        select(models.SubArea.framework_area, models.Agreement.procurement_number)
        .join(models.AgreementSubArea, models.AgreementSubArea.sub_area_id == models.SubArea.id)
        .join(
            models.Agreement,
            models.Agreement.agreement_number == models.AgreementSubArea.agreement_number,
        )
        .where(models.SubArea.framework_area.in_(wanted))
        .distinct()
    )
    result: dict[str, set[str]] = {area: set() for area in wanted}
    for area, procurement in rows:
        result[area].add(procurement)
    missing = [area for area, numbers in result.items() if not numbers]
    if missing:
        known = session.scalars(
            select(models.SubArea.framework_area).distinct().order_by(models.SubArea.framework_area)
        )
        raise UnknownAreaError(
            f"not in the register: {', '.join(missing)}. Known areas: {', '.join(known)}"
        )
    return result


def load_stored_documents(session: Session) -> dict[str, StoredDocument]:
    """Return what earlier runs stored, keyed by document URL."""
    rows = session.scalars(select(models.SourceDocument))
    return {
        row.url: StoredDocument(
            row.url, row.version, row.sha256, row.size_bytes, row.local_path, row.etag
        )
        for row in rows
    }


def save_fetch(
    session: Session,
    pages: Sequence[AgreementPage],
    results: Sequence[FetchResult],
    listed_page_urls: Collection[str],
) -> None:
    """Record the pages, the stored documents and the page-to-document links.

    `listed_page_urls` are all pages the index on avropa.se lists in this run,
    those read and those that could not be read.
    """
    if pages:
        page_rows = [
            {
                "url": page.url,
                "title": page.title,
                "procurement_numbers": list(page.procurement_numbers),
                "agreement_period": page.agreement_period,
            }
            for page in pages
        ]
        statement = insert(models.AgreementPage).values(page_rows)
        session.execute(
            statement.on_conflict_do_update(
                index_elements=["url"],
                set_={
                    "title": statement.excluded.title,
                    "procurement_numbers": statement.excluded.procurement_numbers,
                    "agreement_period": statement.excluded.agreement_period,
                    "checked_at": func.now(),
                },
            )
        )

    stored: Mapping[str, StoredDocument] = {
        result.link.url: result.stored for result in results if result.stored is not None
    }
    # A FAILED result carries the previous file, which is still linked but was not downloaded.
    downloaded = [
        result.stored
        for result in results
        if result.stored is not None
        and result.status in (FetchStatus.NEW, FetchStatus.UPDATED, FetchStatus.UNCHANGED)
    ]
    if downloaded:
        statement = insert(models.SourceDocument).values(
            [
                {
                    "url": document.url,
                    "version": document.version,
                    "etag": document.etag,
                    "file_type": document.local_path.rsplit(".", 1)[-1],
                    "sha256": document.sha256,
                    "size_bytes": document.size_bytes,
                    "local_path": document.local_path,
                }
                for document in downloaded
            ]
        )
        session.execute(
            statement.on_conflict_do_update(
                index_elements=["url"],
                set_={
                    "version": statement.excluded.version,
                    "etag": statement.excluded.etag,
                    "sha256": statement.excluded.sha256,
                    "size_bytes": statement.excluded.size_bytes,
                    "local_path": statement.excluded.local_path,
                    "downloaded_at": func.now(),
                },
            )
        )

    page_urls = [page.url for page in pages]
    if page_urls:
        session.execute(
            delete(models.AgreementPageDocument).where(
                models.AgreementPageDocument.page_url.in_(page_urls)
            )
        )
    link_rows = [
        {
            "page_url": page.url,
            "document_url": link.url,
            "title": link.title,
            "category": link.category,
            "agreement_number": link.agreement_number,
            "site_updated": link.site_updated,
        }
        for page in pages
        for link in page.documents
        if link.url in stored
    ]
    if link_rows:
        session.execute(insert(models.AgreementPageDocument).values(link_rows))

    # A stored page the index does not list gets missing_since, unless an earlier run
    # set it (the first run that missed the page is kept). A listed page gets it
    # cleared, also one that could not be read in this run.
    listed = {*listed_page_urls, *page_urls}
    session.execute(
        update(models.AgreementPage)
        .where(
            models.AgreementPage.url.not_in(listed), models.AgreementPage.missing_since.is_(None)
        )
        .values(missing_since=func.now())
    )
    session.execute(
        update(models.AgreementPage)
        .where(
            models.AgreementPage.url.in_(listed), models.AgreementPage.missing_since.is_not(None)
        )
        .values(missing_since=None)
    )
