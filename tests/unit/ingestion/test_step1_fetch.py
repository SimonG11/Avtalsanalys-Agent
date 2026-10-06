"""Tests for avtalsagent.ingestion.step1_fetch, with a fake HTTP server (no network)."""

import hashlib
from collections.abc import Callable
from datetime import date
from pathlib import Path

import httpx
import pytest

from avtalsagent.domain.documents import AgreementPage, DocumentLink
from avtalsagent.ingestion.step1_fetch import (
    FetchStatus,
    PoliteClient,
    StoredDocument,
    discover_pages,
    fetch_documents,
    select_pages,
)

PDF = b"%PDF-1.7 the agreement"
BASE = "https://www.avropa.se/globalassets/bilagor"
FIXTURES = Path(__file__).parents[2] / "fixtures" / "avropa"
INDEX_URL = "https://www.avropa.se/ramavtal/ramavtal-a-o/"
# The three agreement pages that tests/fixtures/avropa/index.html links to.
AREA = "https://www.avropa.se/ramavtal/ramavtalsomraden/mobler-och-inredning/mobler-och-inredning"


def link(name: str, version: str | None = "v1", file_type: str = "pdf") -> DocumentLink:
    return DocumentLink(
        url=f"{BASE}/{name}.{file_type}",
        version=version,
        title=name,
        category="Avtal",
        agreement_number=None,
        file_type=file_type,
        site_updated=date(2026, 8, 27),
    )


def page(url: str, *numbers: str) -> AgreementPage:
    return AgreementPage(
        url=url, title=url, procurement_numbers=numbers, agreement_period=None, documents=()
    )


class FakeSite:
    """Serves fixed content per URL and records every request."""

    def __init__(self, responses: dict[str, httpx.Response]) -> None:
        self.responses = responses
        self.requested: list[str] = []

    def client(self) -> PoliteClient:
        def handle(request: httpx.Request) -> httpx.Response:
            self.requested.append(str(request.url))
            return self.responses.get(str(request.url), httpx.Response(404))

        return PoliteClient(httpx.Client(transport=httpx.MockTransport(handle)), 0.0)


def stored_from(result_document: StoredDocument | None) -> dict[str, StoredDocument]:
    assert result_document is not None
    return {result_document.url: result_document}


def test_new_document_is_stored_under_its_hash(tmp_path: Path) -> None:
    site = FakeSite({f"{BASE}/huvuddokument.pdf?v=v1": httpx.Response(200, content=PDF)})

    [result] = fetch_documents(site.client(), [link("huvuddokument")], {}, tmp_path, ["pdf"])

    sha256 = hashlib.sha256(PDF).hexdigest()
    assert result.status is FetchStatus.NEW
    assert result.stored == StoredDocument(
        f"{BASE}/huvuddokument.pdf", "v1", sha256, len(PDF), f"documents/{sha256}.pdf"
    )
    assert (tmp_path / f"documents/{sha256}.pdf").read_bytes() == PDF


def test_rerun_with_same_version_makes_no_request(tmp_path: Path) -> None:
    site = FakeSite({f"{BASE}/huvuddokument.pdf?v=v1": httpx.Response(200, content=PDF)})
    [first] = fetch_documents(site.client(), [link("huvuddokument")], {}, tmp_path, ["pdf"])

    [second] = fetch_documents(
        site.client(), [link("huvuddokument")], stored_from(first.stored), tmp_path, ["pdf"]
    )

    assert second.status is FetchStatus.SAME_VERSION
    assert len(site.requested) == 1  # only the first run downloaded


def test_new_version_with_new_content_is_updated(tmp_path: Path) -> None:
    site = FakeSite(
        {
            f"{BASE}/huvuddokument.pdf?v=v1": httpx.Response(200, content=PDF),
            f"{BASE}/huvuddokument.pdf?v=v2": httpx.Response(200, content=PDF + b" amended"),
        }
    )
    [first] = fetch_documents(site.client(), [link("huvuddokument")], {}, tmp_path, ["pdf"])

    [second] = fetch_documents(
        site.client(), [link("huvuddokument", "v2")], stored_from(first.stored), tmp_path, ["pdf"]
    )

    assert second.status is FetchStatus.UPDATED
    assert second.stored is not None and first.stored is not None
    assert second.stored.version == "v2"
    # The old file is kept; the new one is stored next to it.
    assert (tmp_path / first.stored.local_path).is_file()
    assert (tmp_path / second.stored.local_path).is_file()


def test_new_version_with_same_content_is_unchanged(tmp_path: Path) -> None:
    site = FakeSite(
        {
            f"{BASE}/huvuddokument.pdf?v=v1": httpx.Response(200, content=PDF),
            f"{BASE}/huvuddokument.pdf?v=v2": httpx.Response(200, content=PDF),
        }
    )
    [first] = fetch_documents(site.client(), [link("huvuddokument")], {}, tmp_path, ["pdf"])

    [second] = fetch_documents(
        site.client(), [link("huvuddokument", "v2")], stored_from(first.stored), tmp_path, ["pdf"]
    )

    assert second.status is FetchStatus.UNCHANGED
    assert second.stored is not None and second.stored.version == "v2"


def test_missing_file_on_disk_is_downloaded_again(tmp_path: Path) -> None:
    site = FakeSite({f"{BASE}/huvuddokument.pdf?v=v1": httpx.Response(200, content=PDF)})
    [first] = fetch_documents(site.client(), [link("huvuddokument")], {}, tmp_path, ["pdf"])
    assert first.stored is not None
    (tmp_path / first.stored.local_path).unlink()

    [second] = fetch_documents(
        site.client(), [link("huvuddokument")], stored_from(first.stored), tmp_path, ["pdf"]
    )

    assert second.status is FetchStatus.UNCHANGED
    assert (tmp_path / first.stored.local_path).is_file()


def test_file_type_not_chosen_is_excluded_without_request(tmp_path: Path) -> None:
    site = FakeSite({})

    [result] = fetch_documents(
        site.client(), [link("prislista", file_type="xlsx")], {}, tmp_path, ["pdf", "docx"]
    )

    assert result.status is FetchStatus.EXCLUDED
    assert site.requested == []


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(200, content=b"<html>Sidan finns inte</html>"), "not a pdf file"),
        (httpx.Response(404), "download failed"),
    ],
)
def test_bad_download_fails_without_stopping_the_run(
    tmp_path: Path, response: httpx.Response, message: str
) -> None:
    site = FakeSite(
        {
            f"{BASE}/trasig.pdf?v=v1": response,
            f"{BASE}/huvuddokument.pdf?v=v1": httpx.Response(200, content=PDF),
        }
    )

    bad, good = fetch_documents(
        site.client(), [link("trasig"), link("huvuddokument")], {}, tmp_path, ["pdf"]
    )

    assert bad.status is FetchStatus.FAILED
    assert bad.message is not None and message in bad.message
    assert bad.stored is None
    assert good.status is FetchStatus.NEW


def test_same_url_on_two_pages_is_fetched_once(tmp_path: Path) -> None:
    site = FakeSite({f"{BASE}/mall.pdf?v=v1": httpx.Response(200, content=PDF)})

    results = fetch_documents(site.client(), [link("mall"), link("mall")], {}, tmp_path, ["pdf"])

    assert [r.status for r in results] == [FetchStatus.NEW]
    assert len(site.requested) == 1


def test_link_without_version_is_revalidated_with_its_etag(tmp_path: Path) -> None:
    # /contentassets/ links (e.g. the Microsoft volume agreement) have no "?v=".
    url = f"{BASE}/huvuddokument.pdf"
    seen_etags: list[str | None] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen_etags.append(request.headers.get("If-None-Match"))
        if request.headers.get("If-None-Match") == '"etag-1"':
            return httpx.Response(304)
        return httpx.Response(200, content=PDF, headers={"ETag": '"etag-1"'})

    def client() -> PoliteClient:
        return PoliteClient(httpx.Client(transport=httpx.MockTransport(handle)), 0.0)

    [first] = fetch_documents(client(), [link("huvuddokument", None)], {}, tmp_path, ["pdf"])
    [second] = fetch_documents(
        client(), [link("huvuddokument", None)], stored_from(first.stored), tmp_path, ["pdf"]
    )

    assert first.stored is not None and first.stored.url == url
    assert first.stored.etag == '"etag-1"'
    assert second.status is FetchStatus.NOT_MODIFIED
    assert second.stored == first.stored
    assert seen_etags == [None, '"etag-1"']


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(500), "500 Internal Server Error"),
        (httpx.Response(200, text="<html><body>Sidan har flyttat</body></html>"), "no h1"),
    ],
)
def test_page_that_cannot_be_read_is_reported_and_skipped(
    response: httpx.Response, message: str
) -> None:
    index = (FIXTURES / "index.html").read_text(encoding="utf-8")
    agreement_page = (FIXTURES / "agreement_page.html").read_text(encoding="utf-8")
    site = FakeSite(
        {
            INDEX_URL: httpx.Response(200, text=index),
            f"{AREA}/arbetsplats-och-forvaring/": httpx.Response(200, text=agreement_page),
            f"{AREA}/arbetsstolar/": response,
            f"{AREA}/arkiv-och-magasin/": httpx.Response(200, text=agreement_page),
        }
    )

    pages, problems = discover_pages(site.client(), INDEX_URL)

    assert [p.url for p in pages] == [
        f"{AREA}/arbetsplats-och-forvaring/",
        f"{AREA}/arkiv-och-magasin/",
    ]
    assert [p.url for p in problems] == [f"{AREA}/arbetsstolar/"]
    assert message in problems[0].message


def test_pages_are_selected_by_procurement_number() -> None:
    pages = [
        page("it-drift-mindre", "23.3-5890-2023"),
        page("mobler", "23.3-5834-2022"),
        page("hotell"),  # a landing page without numbers
    ]

    selected = select_pages(pages, {"23.3-5890-2023", "23.3-10639-2023"})

    assert [p.url for p in selected] == ["it-drift-mindre"]


def test_polite_client_waits_between_requests() -> None:
    now = [0.0]
    waits: list[float] = []

    def sleep(seconds: float) -> None:
        waits.append(seconds)
        now[0] += seconds

    clock: Callable[[], float] = lambda: now[0]  # noqa: E731
    client = PoliteClient(
        httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200))),
        delay_seconds=0.5,
        sleep=sleep,
        clock=clock,
    )

    client.get("https://www.avropa.se/a")
    now[0] += 0.2  # some work between the requests
    client.get("https://www.avropa.se/b")

    assert waits == [pytest.approx(0.3)]
    assert client.request_count == 2
