"""What the step-5 checks read: the files, their links, and the register.

What:
    `CheckContext` holds everything the checks compare: each processed file
    (`CheckedFile`: what step 4 found, its links, its sections and pages),
    every link of the catalog, the register and the framework areas of the
    run. Its methods look things up the same way for every check: the register
    entries of a case or agreement number by key, the procurements of the
    pages that link to a file, and the scope of a page.

Why:
    The checks are pure functions of these values, so each can be tested
    without a database, and the lookups that decide what "the same number" or
    "the page's sub-area" mean are written once.

How:
    Numbers are compared by key (`domain/identifiers.py`), never as written:
    "23.3.2940-20:018" and "23.3-2940-20:018" are the same agreement. The scope
    of a page is the register entries of its procurements that have a sub-area
    level with the page's title, ignoring case: the page "IT-konsulttjänster 3.
    IT-säkerhet" is the sub-area "IT-konsulttjänster 3. IT-säkerhet" of
    23.3-8321-2024. Every pilot page has such entries (56 of 224 for each of
    the four Bemanningstjänster pages). A page without them has no scope, not
    the whole procurement: renamed to "5. IT-konsultlösningar", the page of
    sub-area 5 of 23.3-1688-2024 would otherwise be compared with the dates of
    sub-areas 1 and 5 together and let its main document cover sub-area 1's
    agreements. `agreement_period` reports such a page, and neither it nor
    `coverage` compares anything through it.
"""

from collections import defaultdict
from dataclasses import dataclass
from functools import cached_property

from avtalsagent.domain.documents import CatalogLink
from avtalsagent.domain.extracted import DocumentExtraction, DocumentMetadata, Fact
from avtalsagent.domain.identifiers import agreement_key, procurement_key
from avtalsagent.domain.parsed import Section
from avtalsagent.domain.register import RegisterEntry


@dataclass(frozen=True)
class CheckedFile:
    """One file that went through steps 2-4."""

    extraction: DocumentExtraction
    links: tuple[CatalogLink, ...]  # the links to it, one per page and title
    sections: tuple[Section, ...]
    file_type: str  # "pdf" or "docx"
    page_count: int  # PDF pages; 0 for a Word file
    ocr_pages: tuple[int, ...]  # PDF pages without a text layer (step 2)

    @property
    def sha256(self) -> str:
        return self.extraction.metadata.sha256

    @property
    def metadata(self) -> DocumentMetadata:
        return self.extraction.metadata

    @property
    def facts(self) -> tuple[Fact, ...]:
        return self.extraction.facts

    @property
    def page_urls(self) -> tuple[str, ...]:
        """The agreement pages that link to the file, in link order, each once."""
        return tuple(dict.fromkeys(link.page_url for link in self.links))


@dataclass(frozen=True)
class CheckContext:
    """Everything the step-5 checks compare."""

    files: tuple[CheckedFile, ...]
    # Every link of the catalog, also to files that are not in `files` (not parsed).
    links: tuple[CatalogLink, ...]
    register: tuple[RegisterEntry, ...]
    # The framework areas of the run, as named in the register ("IT-drift"); the
    # coverage check looks for documents of their agreements.
    areas: tuple[str, ...]

    # Built on first use; a frozen dataclass allows this, as cached_property writes
    # to the instance's __dict__ directly.
    @cached_property
    def _by_procurement(self) -> dict[str, list[RegisterEntry]]:
        entries: defaultdict[str, list[RegisterEntry]] = defaultdict(list)
        for entry in self.register:
            if key := procurement_key(entry.procurement_number):
                entries[key].append(entry)
        return dict(entries)

    @cached_property
    def _by_agreement(self) -> dict[str, list[RegisterEntry]]:
        entries: defaultdict[str, list[RegisterEntry]] = defaultdict(list)
        for entry in self.register:
            if key := agreement_key(entry.agreement_number):
                entries[key].append(entry)
        return dict(entries)

    def procurement_entries(self, number: str) -> list[RegisterEntry]:
        """The register entries of a case number, in any spelling; [] when it has none."""
        key = procurement_key(number)
        return self._by_procurement.get(key, []) if key else []

    def agreement_entries(self, number: str) -> list[RegisterEntry]:
        """The register entries (one per sub-area) of an agreement number, in any spelling."""
        key = agreement_key(number)
        return self._by_agreement.get(key, []) if key else []

    def page_procurements(self, file: CheckedFile) -> set[str]:
        """The keys of the procurement numbers of the pages that link to the file."""
        return {
            key
            for link in file.links
            for number in link.page_procurement_numbers
            if (key := procurement_key(number))
        }

    def page_entries(self, link: CatalogLink) -> list[RegisterEntry]:
        """The register entries of the procurements of the link's page."""
        return [
            entry
            for number in link.page_procurement_numbers
            for entry in self.procurement_entries(number)
        ]

    def page_scope(self, link: CatalogLink) -> list[RegisterEntry]:
        """The register entries of the sub-area the link's page is for (module docstring).

        [] when the page's title is no sub-area level of its procurements' entries.
        """
        title = link.page_title.casefold()
        return [
            entry
            for entry in self.page_entries(link)
            if any(level.casefold() == title for level in entry.sub_area_path)
        ]
