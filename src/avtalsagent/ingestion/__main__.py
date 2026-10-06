"""Command line for the ingestion workflow.

What:
    One command per step:
    `fetch` (step 1) reads the agreement pages on avropa.se and downloads the
    documents of the chosen framework areas; `--area` (repeatable) overrides
    the areas in the settings.
    `parse` (step 2) reads every downloaded file with Docling and reports pages
    without a text layer.
    `chunk` (step 3) splits the parsed files into sections and chunks, stores
    them and reports the files whose table of contents lists a number that is
    not a section.
    `outline` prints a file's table of contents as found by step 3, or a list
    of all files when no hash is given, so the result can be checked by hand.
    `verify` checks the sections of every parsed PDF against its text layer
    (`ingestion/text_layer_check.py`); it reads the files in the data
    directory and needs no database.

Why:
    One command per step makes each step easy to run and check on its own.

How:
    The register must be loaded first (M1), since the framework areas are
    turned into procurement numbers through it. Downloads and parsing happen
    outside any database transaction, so a long run never holds one open; the
    results are then saved in one transaction.
"""

import argparse
from collections import Counter

from sqlalchemy import select

from avtalsagent.config import get_settings
from avtalsagent.db import models
from avtalsagent.db.session import create_db_engine, session_factory
from avtalsagent.ingestion.catalog import (
    load_stored_documents,
    procurements_for_areas,
    save_fetch,
)
from avtalsagent.ingestion.section_store import (
    document_links,
    load_outline,
    save_sections,
    source_files,
)
from avtalsagent.ingestion.step1_fetch import (
    FetchStatus,
    create_client,
    discover_pages,
    fetch_documents,
    select_pages,
)
from avtalsagent.ingestion.step2_parse import ParseStatus, load_cached, parse_files
from avtalsagent.ingestion.step3_chunk import OutlineKind, chunk_document, document_context
from avtalsagent.ingestion.text_layer_check import DocumentCheck, check_document, text_layer

_SHOWN_FILES = 10  # files with the most missing lines, printed with examples
_SHOWN_LINES = 3


def fetch(areas: list[str]) -> None:
    settings = get_settings()
    factory = session_factory(create_db_engine())
    with factory() as session:
        by_area = procurements_for_areas(session, areas)
        stored = load_stored_documents(session)
    wanted = set().union(*by_area.values())

    http = create_client(settings.fetch_delay_seconds)
    pages, problems = discover_pages(http, settings.agreement_index_url)
    selected = select_pages(pages, wanted)
    links = [link for page in selected for link in page.documents]
    results = fetch_documents(http, links, stored, settings.data_dir, settings.fetch_file_types)

    with factory.begin() as session:
        listed = [page.url for page in pages] + [problem.url for problem in problems]
        save_fetch(session, selected, results, listed)

    print(f"Read {len(pages)} agreement pages ({http.request_count} requests in total)")
    for problem in problems:
        print(f"  page not read: {problem.url}: {problem.message}")
    for area, numbers in by_area.items():
        area_pages = [page for page in selected if numbers.intersection(page.procurement_numbers)]
        found = set().union(*(page.procurement_numbers for page in area_pages))
        print(f"{area}: {len(area_pages)} pages, procurements {', '.join(sorted(numbers))}")
        for number in sorted(numbers - found):
            print(f"  no page found for procurement {number}")
    counts = Counter(result.status for result in results)
    print(f"Documents: {len(results)} unique links")
    for status in FetchStatus:
        print(f"  {status.value}: {counts.get(status, 0)}")
    for result in results:
        if result.status is FetchStatus.FAILED:
            print(f"  failed: {result.link.url}: {result.message}")


def parse() -> None:
    # Imported here, so the other commands start without loading Docling's models.
    from avtalsagent.ingestion.parsers.docling_parser import DoclingParser

    settings = get_settings()
    with session_factory(create_db_engine())() as session:
        files = source_files(session, settings.data_dir)
    parser = DoclingParser()
    print(f"Parsing {len(files)} files with {parser.name}")
    counts: Counter[ParseStatus] = Counter()
    pages = 0
    for number, result in enumerate(parse_files(files, parser, settings.parsed_dir), start=1):
        counts[result.status] += 1
        name = result.file.path.name[:12]
        if result.document is None:
            print(f"  [{number}/{len(files)}] {name}: failed: {result.message}", flush=True)
            continue
        pages += len(result.document.pages)
        ocr = result.document.pages_needing_ocr
        note = f", pages without text layer: {ocr}" if ocr else ""
        print(
            f"  [{number}/{len(files)}] {name}: {result.status.value}, "
            f"{len(result.document.blocks)} blocks{note}",
            flush=True,
        )
    print(f"Files: {len(files)} ({pages} PDF pages)")
    for status in ParseStatus:
        print(f"  {status.value}: {counts.get(status, 0)}")


def chunk() -> None:
    settings = get_settings()
    factory = session_factory(create_db_engine())
    with factory() as session:
        files = source_files(session, settings.data_dir)
        links = document_links(session)
    parsed = []
    for file in files:
        document = load_cached(settings.parsed_dir, file.sha256)
        if document is None:
            print(f"  not parsed (run `parse` first): {file.path.name}")
        elif file.sha256 not in links:
            print(f"  no page links to it any more, skipped: {file.path.name}")
        else:
            parsed.append(document)
    chunked = [
        chunk_document(document, document_context(links[document.sha256])) for document in parsed
    ]
    with factory.begin() as session:
        save_sections(session, parsed, chunked)

    outlines = Counter(document.outline for document in chunked)
    print(f"Files: {len(chunked)} of {len(files)}")
    for kind in OutlineKind:
        print(f"  {kind.value}: {outlines.get(kind, 0)}")
    print(f"Sections: {sum(len(document.sections) for document in chunked)}")
    print(f"Chunks: {sum(len(document.chunks) for document in chunked)}")
    with_contents = [result for result in chunked if result.contents_missing is not None]
    complete = sum(1 for result in with_contents if not result.contents_missing)
    print(
        f"Files with a table of contents: {len(with_contents)}, all its numbers found: {complete}"
    )
    for checked in with_contents:
        if checked.contents_missing:
            print(f"  {checked.sha256[:12]}: listed but not found {checked.contents_missing}")
    for document in parsed:
        if document.pages_needing_ocr:
            print(
                f"  {document.sha256[:12]}: pages without text layer {document.pages_needing_ocr}"
            )


def outline(sha256_prefix: str | None) -> None:
    factory = session_factory(create_db_engine())
    with factory() as session:
        links = document_links(session)
        if sha256_prefix is None:
            for row in session.scalars(
                select(models.ParsedFile).order_by(models.ParsedFile.sha256)
            ):
                context = document_context(links.get(row.sha256, []))
                print(
                    f"{row.sha256[:12]}  {row.outline:9} {row.section_count:4} sections  "
                    f"{context.document} | {context.agreement}"
                )
            return
        sections = load_outline(session, sha256_prefix)
    if not sections:
        print(f"no sections for a file starting with {sha256_prefix}")
        return
    matches = sorted({section.sha256 for section in sections})
    if len(matches) > 1:
        print(f"{len(matches)} files start with {sha256_prefix}; give more of the hash:")
        for sha256 in matches:
            print(f"  {sha256}")
        return
    context = document_context(links.get(sections[0].sha256, []))
    print(f"{context.document} | {context.agreement} | {sections[0].sha256}")
    for section in sections:
        heading = f"{section.number} {section.title}" if section.number else section.title
        pages = f"p. {section.page_start}" if section.page_start else ""
        if section.page_end and section.page_end != section.page_start:
            pages += f"-{section.page_end}"
        print(f"{'  ' * max(section.level - 1, 0)}{heading}  [{pages}]")


def verify() -> None:
    settings = get_settings()
    checks: list[DocumentCheck] = []
    for path in sorted(settings.parsed_dir.glob("*.json")):
        document = load_cached(settings.parsed_dir, path.stem)
        pdf = settings.data_dir / "documents" / f"{path.stem}.pdf"
        if document is None or document.file_type != "pdf" or not pdf.is_file():
            continue
        checks.append(check_document(document, text_layer(pdf)))

    lines = sum(check.lines.checked for check in checks)
    removed = sum(check.lines.removed for check in checks)
    missing = sum(len(check.lines.missing) for check in checks)
    in_sections = lines - removed - missing
    print(f"PDF files: {len(checks)}")
    print(f"Text-layer lines: {lines}")
    print(f"  in a section: {in_sections} ({in_sections / max(lines, 1):.1%})")
    print(
        "  only in text step 3 removes on purpose "
        f"(headers, footers, contents, e-signature certificates): {removed}"
    )
    print(f"  missing: {missing} ({missing / max(lines, 1):.1%})")
    worst = sorted(checks, key=lambda check: len(check.lines.missing), reverse=True)
    for check in worst[:_SHOWN_FILES]:
        if check.lines.missing:
            print(
                f"  {check.sha256[:12]}: {len(check.lines.missing)} of "
                f"{check.lines.checked} lines missing"
            )
            for line in check.lines.missing[:_SHOWN_LINES]:
                print(f"      p. {line.page}: {line.text[:100]}")

    logs = [check for check in checks if check.questions is not None]
    questions = sum(check.questions or 0 for check in logs)
    sections = sum(check.question_sections or 0 for check in logs)
    print(
        f"Questions logs: {len(logs)}, questions in the text layer: {questions}, "
        f"sections: {sections}"
    )
    for check in logs:
        if check.questions != check.question_sections:
            print(
                f"  {check.sha256[:12]}: {check.questions} questions, "
                f"{check.question_sections} sections"
            )

    gaps = [check for check in checks if check.gaps]
    print(
        f"Numbered lines between sections that are not sections: "
        f"{sum(len(check.gaps) for check in gaps)} in {len(gaps)} files"
    )
    for check in gaps:
        for line in check.gaps:
            print(f"  {check.sha256[:12]} p. {line.page}: {line.text[:100]}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingestion workflow for avropa.se documents.")
    steps = parser.add_subparsers(dest="step", required=True)
    fetch_parser = steps.add_parser("fetch", help="step 1: download the documents")
    fetch_parser.add_argument(
        "--area",
        action="append",
        help="framework area as named in the register (repeatable); default from settings",
    )
    steps.add_parser("parse", help="step 2: read the downloaded files with Docling")
    steps.add_parser("chunk", help="step 3: split the parsed files into sections and chunks")
    outline_parser = steps.add_parser("outline", help="print a file's table of contents")
    outline_parser.add_argument("sha256", nargs="?", help="the file's hash or its beginning")
    steps.add_parser("verify", help="check the sections against each PDF's text layer")
    args = parser.parse_args()
    if args.step == "fetch":
        fetch(args.area or get_settings().fetch_areas)
    elif args.step == "parse":
        parse()
    elif args.step == "chunk":
        chunk()
    elif args.step == "outline":
        outline(args.sha256)
    elif args.step == "verify":
        verify()


if __name__ == "__main__":
    main()
