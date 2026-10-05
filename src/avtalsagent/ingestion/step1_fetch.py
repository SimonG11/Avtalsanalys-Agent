"""Ingestion step 1: fetch the agreement documents from avropa.se.

What:
    `discover_pages` reads the A-Ö index and every agreement page it links to.
    `select_pages` keeps the pages whose procurement numbers belong to the
    chosen framework areas. `fetch_documents` downloads each linked file, stores
    it under its SHA-256 hash and reports what happened to it.

Why:
    The agent can only cite documents that are on disk with known origin. A
    rerun must not download everything again: avropa.se gives most links a
    version ("?v=..."), so a document whose version is unchanged is skipped
    without a request. Links without a version are asked for with the ETag
    from the last download (If-None-Match), and the site answers 304 Not
    Modified if the file is the same. A downloaded file whose hash is
    unchanged is reported as unchanged. Storing files by hash means the same file linked
    from several pages is stored once, and a file is never overwritten in place.

How:
    All requests go through `PoliteClient`, which waits between requests and
    identifies the program. The functions take what an earlier run stored as
    a plain mapping, so they can be tested without a database;
    `ingestion/catalog.py` reads and writes that state in Postgres. One bad
    document never stops the run: it becomes a FAILED result with the reason.
"""

import hashlib
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import httpx

from avtalsagent.domain.documents import AgreementPage, DocumentLink
from avtalsagent.ingestion.avropa_pages import PageLayoutError, parse_agreement_page, parse_index

USER_AGENT = "avtalsagent/0.1 (+https://github.com/SimonG11/Avtalsanalys-Agent)"

# The first bytes of each file type, so an error page is never stored as a document.
# docx (and xlsx) files are zip archives.
_MAGIC_BYTES = {"pdf": b"%PDF", "docx": b"PK\x03\x04", "xlsx": b"PK\x03\x04"}


class PoliteClient:
    """An HTTP client that waits `delay_seconds` between requests."""

    def __init__(
        self,
        client: httpx.Client,
        delay_seconds: float,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._delay = delay_seconds
        self._sleep = sleep
        self._clock = clock
        self._last_request: float | None = None
        self.request_count = 0

    def get(self, url: str, headers: Mapping[str, str] | None = None) -> httpx.Response:
        """GET `url`; raise for an error status. 304 Not Modified is returned, not raised."""
        if self._last_request is not None:
            wait = self._delay - (self._clock() - self._last_request)
            if wait > 0:
                self._sleep(wait)
        self.request_count += 1
        try:
            response = self._client.get(url, headers=headers)
        finally:
            self._last_request = self._clock()
        if response.status_code != httpx.codes.NOT_MODIFIED:
            response.raise_for_status()
        return response


def create_client(delay_seconds: float) -> PoliteClient:
    """A `PoliteClient` over a real httpx client, with redirects followed."""
    client = httpx.Client(follow_redirects=True, timeout=60.0, headers={"User-Agent": USER_AGENT})
    return PoliteClient(client, delay_seconds)


@dataclass(frozen=True)
class PageProblem:
    url: str
    message: str


def discover_pages(
    http: PoliteClient, index_url: str
) -> tuple[list[AgreementPage], list[PageProblem]]:
    """Read the index and every agreement page. Pages that cannot be read are reported."""
    page_urls = parse_index(http.get(index_url).text, index_url)
    pages: list[AgreementPage] = []
    problems: list[PageProblem] = []
    for url in page_urls:
        try:
            pages.append(parse_agreement_page(http.get(url).text, url))
        except (httpx.HTTPError, PageLayoutError) as error:
            problems.append(PageProblem(url, str(error)))
    return pages, problems


def select_pages(
    pages: Iterable[AgreementPage], procurement_numbers: set[str]
) -> list[AgreementPage]:
    """Keep the pages that state at least one of the given procurement numbers."""
    return [page for page in pages if procurement_numbers.intersection(page.procurement_numbers)]


class FetchStatus(StrEnum):
    NEW = "new"  # downloaded, not seen before
    UPDATED = "updated"  # downloaded, content differs from the stored file
    UNCHANGED = "unchanged"  # downloaded, same content as the stored file
    SAME_VERSION = "same version"  # not downloaded: same "?v=" as last time
    NOT_MODIFIED = "not modified"  # not downloaded: the site answered 304 to our ETag
    EXCLUDED = "excluded"  # not downloaded: file type not in the chosen types
    FAILED = "failed"  # download failed or the content was not the expected type


@dataclass(frozen=True)
class StoredDocument:
    """What an earlier run stored for a document URL."""

    url: str
    version: str | None
    sha256: str
    size_bytes: int
    local_path: str  # relative to the data directory
    etag: str | None = None  # the site's ETag header, for links without a version


@dataclass(frozen=True)
class FetchResult:
    link: DocumentLink
    status: FetchStatus
    stored: StoredDocument | None  # the file on disk after this run, if any
    message: str | None = None


def fetch_documents(
    http: PoliteClient,
    links: Iterable[DocumentLink],
    stored: Mapping[str, StoredDocument],
    data_dir: Path,
    file_types: Iterable[str],
) -> list[FetchResult]:
    """Download each link that is new or has a new version; one result per unique URL."""
    allowed = {file_type.lower() for file_type in file_types}
    results: list[FetchResult] = []
    seen: set[str] = set()
    for link in links:
        if link.url in seen:
            continue
        seen.add(link.url)
        results.append(_fetch_one(http, link, stored.get(link.url), data_dir, allowed))
    return results


def _fetch_one(
    http: PoliteClient,
    link: DocumentLink,
    previous: StoredDocument | None,
    data_dir: Path,
    allowed: set[str],
) -> FetchResult:
    if link.file_type not in allowed:
        return FetchResult(link, FetchStatus.EXCLUDED, None)
    on_disk = previous is not None and (data_dir / previous.local_path).is_file()
    if on_disk and previous and link.version is not None and previous.version == link.version:
        return FetchResult(link, FetchStatus.SAME_VERSION, previous)

    url = link.url if link.version is None else f"{link.url}?v={link.version}"
    headers = (
        {"If-None-Match": previous.etag}
        if on_disk and previous and link.version is None and previous.etag
        else None
    )
    try:
        response = http.get(url, headers)
    except httpx.HTTPError as error:
        return FetchResult(link, FetchStatus.FAILED, previous, f"download failed: {error}")
    if response.status_code == httpx.codes.NOT_MODIFIED and previous is not None:
        return FetchResult(link, FetchStatus.NOT_MODIFIED, previous)
    content = response.content
    magic = _MAGIC_BYTES.get(link.file_type)
    if magic is not None and not content.startswith(magic):
        return FetchResult(link, FetchStatus.FAILED, previous, f"not a {link.file_type} file")

    document = _store(content, link, data_dir, response.headers.get("ETag"))
    if previous is None:
        status = FetchStatus.NEW
    elif previous.sha256 == document.sha256:
        status = FetchStatus.UNCHANGED
    else:
        status = FetchStatus.UPDATED
    return FetchResult(link, status, document)


def _store(content: bytes, link: DocumentLink, data_dir: Path, etag: str | None) -> StoredDocument:
    """Write the file to documents/<sha256>.<type>, unless that file already exists."""
    sha256 = hashlib.sha256(content).hexdigest()
    relative = f"documents/{sha256}.{link.file_type}"
    target = data_dir / relative
    if not target.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_suffix(".part")
        partial.write_bytes(content)
        partial.replace(target)
    return StoredDocument(link.url, link.version, sha256, len(content), relative, etag)
