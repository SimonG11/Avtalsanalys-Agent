"""Ingestion steps 3-5 on plain values: split, extract, validate.

What:
    `process` takes the parsed files, the catalog's links, the register and the
    run's framework areas, and runs step 3 (sections and chunks), step 4
    (metadata, facts, references) and step 5 (findings, quarantine, coverage)
    on them. It returns everything in one `IngestionResult`, which the
    `process` and `run` commands save in one transaction and turn into the
    ingestion report.

Why:
    One function with no database and no network (the language model comes in
    as a plain function) is what the commands, the end-to-end test and the
    pilot measurement all call, so the numbers in docs/steg/04-extraktion.md
    come from the same code that runs in production.

How:
    A parsed file that no page links to any more is left out (and so leaves
    the index); its hash is listed in `unlinked`. A link to a file that was
    never parsed stays in the check context, where step 5 holds it back, and
    in the result, where the report finds a page's title by its address. Step
    3's context header is built from the links and the register as
    `section_store.document_links` builds it from the database.
"""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from avtalsagent.domain.documents import CatalogLink
from avtalsagent.domain.identifiers import agreement_key, procurement_key
from avtalsagent.domain.parsed import ParsedDocument
from avtalsagent.domain.register import RegisterEntry
from avtalsagent.ingestion.checks.context import CheckContext, CheckedFile
from avtalsagent.ingestion.extract.title_matcher import TitleMatcher
from avtalsagent.ingestion.step3_chunk import (
    ChunkedDocument,
    LinkInfo,
    chunk_document,
    document_context,
)
from avtalsagent.ingestion.step4_extract import (
    CorpusExtraction,
    ExtractionInput,
    RegisterSpelling,
    extract_corpus,
)
from avtalsagent.ingestion.step5_validate import AcceptedFinding, Validation, validate


@dataclass(frozen=True)
class IngestionResult:
    """Everything steps 3-5 produced in one run."""

    parsed: list[ParsedDocument]  # the files processed, in input order
    chunked: list[ChunkedDocument]  # their sections and chunks, in the same order
    unlinked: list[str]  # parsed files no page links to any more, left out
    corpus: CorpusExtraction  # step 4
    validation: Validation  # step 5
    # Every link passed in, also to files not parsed: the report names a page by its title.
    links: list[CatalogLink]


def process(
    parsed: Sequence[ParsedDocument],
    links: Sequence[CatalogLink],
    register: Sequence[RegisterEntry],
    areas: Sequence[str],
    accepted: Mapping[str, AcceptedFinding],
    matcher: TitleMatcher | None = None,
) -> IngestionResult:
    """Run steps 3, 4 and 5 on the parsed files."""
    links_by_sha: defaultdict[str, list[CatalogLink]] = defaultdict(list)
    for link in links:
        links_by_sha[link.sha256].append(link)
    linked = [document for document in parsed if document.sha256 in links_by_sha]
    unlinked = [document.sha256 for document in parsed if document.sha256 not in links_by_sha]

    infos = link_infos(links_by_sha, register)
    chunked = [
        chunk_document(document, document_context(infos[document.sha256])) for document in linked
    ]
    inputs = [
        ExtractionInput(document, split, tuple(links_by_sha[document.sha256]))
        for document, split in zip(linked, chunked, strict=True)
    ]
    corpus = extract_corpus(inputs, RegisterSpelling.of(register), matcher)

    files = tuple(
        CheckedFile(
            extraction=extraction,
            links=item.links,
            sections=tuple(item.chunked.sections),
            file_type=item.parsed.file_type,
            page_count=len(item.parsed.pages),
            ocr_pages=tuple(item.parsed.pages_needing_ocr),
        )
        for item, extraction in zip(inputs, corpus.extractions, strict=True)
    )
    context = CheckContext(
        files=files, links=tuple(links), register=tuple(register), areas=tuple(areas)
    )
    return IngestionResult(
        parsed=linked,
        chunked=chunked,
        unlinked=unlinked,
        corpus=corpus,
        validation=validate(context, accepted),
        links=list(links),
    )


def link_infos(
    links_by_sha: Mapping[str, Sequence[CatalogLink]], register: Sequence[RegisterEntry]
) -> dict[str, list[LinkInfo]]:
    """Step 3's view of each file's links, with the register's framework areas and supplier."""
    areas: defaultdict[str, set[str]] = defaultdict(set)
    suppliers: dict[str, str] = {}
    for entry in register:
        if key := procurement_key(entry.procurement_number):
            areas[key].add(entry.framework_area)
        if key := agreement_key(entry.agreement_number):
            suppliers.setdefault(key, entry.supplier_name)

    def info(link: CatalogLink) -> LinkInfo:
        keys = [procurement_key(number) for number in link.page_procurement_numbers]
        card = agreement_key(link.agreement_number) if link.agreement_number else None
        return LinkInfo(
            title=link.title,
            agreement_number=link.agreement_number,
            supplier_name=suppliers.get(card) if card else None,
            page_title=link.page_title,
            procurement_numbers=link.page_procurement_numbers,
            framework_areas=tuple(sorted({area for key in keys if key for area in areas[key]})),
        )

    return {sha256: [info(link) for link in links] for sha256, links in links_by_sha.items()}
