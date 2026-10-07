"""Command line for the ingestion workflow.

What:
    One command per step, and one for the whole workflow:
    `fetch` (step 1) reads the agreement pages on avropa.se and downloads the
    documents of the chosen framework areas.
    `parse` (step 2) reads every downloaded file with Docling and reports pages
    without a text layer.
    `process` (steps 3-5) splits the parsed files into sections and chunks,
    reads their metadata, facts and references, checks them against the
    register and the agreement pages, saves it all and writes the ingestion
    report to data/reports/ (`<start>-inlasning.md` and `.json`). It empties
    the search index, which belongs to the sections it replaces.
    `index` (step 6) builds the search index: it embeds every chunk the
    quarantine lets through and weights its words for BM25.
    `run` does `fetch`, `parse`, `process` and `index` in turn.
    `fetch`, `process` and `run` take `--area` (repeatable) to override the
    framework areas in the settings: `fetch` downloads their documents, and
    the coverage check of `process` looks for a document of each of their
    agreements.
    `search "<fråga>"` prints the sections the hybrid search finds, optionally
    limited to a framework area (`--area`) or an agreement (`--agreement`), for
    checks by hand and as a fallback in a demo.
    `outline` prints a file's table of contents as found by step 3, or a list
    of all files when no hash is given, so the result can be checked by hand.
    `verify` checks the sections of every parsed PDF against its text layer
    (`ingestion/text_layer_check.py`); it reads the files in the data
    directory and needs no database.

Why:
    One command per step makes each step easy to run and check on its own.
    Steps 3-5 are one command because their results belong together: the
    findings of step 5 point at the sections of step 3, and the index (step
    6) leaves out what they hold back. They are saved in one transaction, so
    a failed run leaves the previous run's results in place, and the stored
    findings always belong to the stored sections. The same transaction
    empties the search index, so a search never finds a chunk that a new
    finding holds back, or an old version of a section: it fails until
    `index` has run (fail closed). The report is built and rendered before
    that transaction, so a run whose report fails saves nothing either, and
    written after the commit, so a report on disk is always of a saved run.
    If writing it fails, the saved run stands, and `process` can be run again
    to write it.

How:
    The register must be loaded first (M1), since the framework areas are
    turned into procurement numbers through it and step 5 compares with it.
    Downloads, parsing, steps 3-5 and embedding run outside any database
    transaction, so a long run never holds one open; `fetch` and `process`
    then save their results in one transaction each. `process` runs
    `pipeline.process` on plain values: the parsed files from the parse
    cache, and the catalog, the register and its edition read from the
    database. The language model that chooses headings
    (`llm_title_matcher.py`) is used only when OPENAI_API_KEY is set, and its
    answers are cached, so a second run makes no calls. The deviations a
    person has accepted are read from `accepted_findings.toml`
    (`ACCEPTED_FINDINGS_FILE`; a relative path is read from the working
    directory). A missing file accepts nothing, so `process` logs a warning
    for it: with a mistyped path, every accepted deviation would otherwise
    hold its document back again without a word.

    `index` needs OPENAI_API_KEY for the embeddings, and so does `run`, which
    checks for it before it starts; without it they stop with a message.
    `index` reads the chunks, the quarantine and each file's scope
    (`step6_index.py`), embeds only the texts the embedding cache lacks, in
    batches of EMBEDDING_BATCH_SIZE, and saves each batch to the cache at
    once, so a run that fails keeps what it has paid for. Then, in one
    transaction, it reads the chunks, the quarantine and the scopes again
    and saves the index only if they are unchanged: a `process` run while
    the texts were embedded would otherwise give an index of chunks that are
    gone or newly held back. `search` refuses an index built with another
    embedding model or text analyser (`retrieval/hybrid_search.py`).

    `search` stops with a message for a blank question, and argparse refuses
    a `--limit` below 1. `fetch`, `parse`, `outline`, `verify` and `search`
    print their results, also when `run` calls the first two. `process`,
    `index` and `run` log one line per step with its counts (`logging` at
    LOG_LEVEL; other libraries log warnings only), since the details are in
    the report. Step 3's line counts the pages left out as e-signature
    certificates (in the pilot 38 pages in 25 files); step 6's counts the
    texts taken from the cache and the texts embedded. The OpenAI key is
    never printed or logged: only whether the model is used, and its name.
"""

import argparse
import logging
import time
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from avtalsagent.config import Settings, get_settings
from avtalsagent.db import models
from avtalsagent.db.session import create_db_engine, session_factory
from avtalsagent.domain.extracted import DocumentType, ReferenceStatus, Severity
from avtalsagent.domain.parsed import ParsedDocument
from avtalsagent.domain.register import RegisterEntry
from avtalsagent.domain.search import DocumentScope, IndexPlan, SearchFilters
from avtalsagent.ingestion import pipeline
from avtalsagent.ingestion.catalog import (
    UnknownAreaError,
    load_stored_documents,
    procurements_for_areas,
    save_fetch,
)
from avtalsagent.ingestion.checks.coverage import CoverageStatus
from avtalsagent.ingestion.extract.reference_resolver import counts_in_rate, resolution_rate
from avtalsagent.ingestion.extraction_store import (
    catalog_links,
    quarantine,
    register_entries,
    register_version,
    save_extraction,
    save_findings,
)
from avtalsagent.ingestion.index_store import (
    cached_embeddings,
    clear_index,
    files_gone_since_checked,
    index_sources,
    lock_index,
    metadata_agreement_numbers,
    metadata_document_types,
    save_cached_embeddings,
    save_index,
)
from avtalsagent.ingestion.llm_title_matcher import LanguageModelError, openai_title_matcher
from avtalsagent.ingestion.report import RunInfo, build_report, render_report, write_report
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
from avtalsagent.ingestion.step2_parse import ParseStatus, SourceFile, load_cached, parse_files
from avtalsagent.ingestion.step3_chunk import (
    OutlineKind,
    document_context,
    signature_certificate_page,
)
from avtalsagent.ingestion.step5_validate import load_accepted
from avtalsagent.ingestion.step6_index import document_scopes, plan_index
from avtalsagent.ingestion.text_layer_check import DocumentCheck, check_document, text_layer
from avtalsagent.retrieval import swedish_text
from avtalsagent.retrieval.bm25 import build_term_index
from avtalsagent.retrieval.embedder import Embedder, EmbeddingError, openai_embedder
from avtalsagent.retrieval.hybrid_search import IndexNotReadyError, SectionHit
from avtalsagent.retrieval.hybrid_search import search as search_sections

# Named, not `__name__`: run with `python -m`, this module's name is "__main__".
_log = logging.getLogger("avtalsagent.ingestion")
_LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"

_SHOWN_FILES = 10  # files with the most missing lines, printed with examples
_SHOWN_LINES = 3
_PROGRESS_BATCHES = 10  # step 6 logs its progress every this many embedding requests
_SNIPPET_CHARS = 300  # of a hit's best chunk, printed by `search`


class CommandError(RuntimeError):
    """A command cannot run or finish; the message says why and what to do."""


def fetch(areas: Sequence[str]) -> Counter[FetchStatus]:
    """Step 1; prints what it found and returns the number of links per status."""
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
    return counts


def parse() -> Counter[ParseStatus]:
    """Step 2; prints each file's result and returns the number of files per status."""
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
    return counts


def process(areas: Sequence[str]) -> None:
    """Steps 3-5 on every parsed file, saved in one transaction, and the report."""
    settings = get_settings()
    started_at = datetime.now(UTC)
    factory = session_factory(create_db_engine())
    with factory() as session:
        files = source_files(session, settings.data_dir)
        links = catalog_links(session)
        register = register_entries(session)
        version = register_version(session)
    check_areas(areas, register)
    parsed, unparsed = load_parsed(files, settings.parsed_dir)
    for file in unparsed:
        _log.warning("Not parsed, so not read (run `parse` first): %s", file.path.name)
    if not settings.accepted_findings_file.is_file():
        _log.warning(
            "No accepted findings file at %s, so no deviation is accepted",
            settings.accepted_findings_file,
        )
    accepted = load_accepted(settings.accepted_findings_file)
    matcher = openai_title_matcher(settings)
    model = settings.extraction_model if matcher is not None else None
    _log.info(
        "Input: %d of %d downloaded files parsed, %d links, register %s (%d entries), "
        "%d accepted deviations, language model %s",
        len(parsed),
        len(files),
        len(links),
        version.list_date if version else "none",
        len(register),
        len(accepted),
        model or "not used (no OPENAI_API_KEY)",
    )

    result = pipeline.process(parsed, links, register, areas, accepted, matcher)
    for line in step_lines(result):
        _log.info(line)

    # Rendered before the save, so a report that fails saves nothing; its end is that of
    # steps 3-5. Written after the commit, so a report on disk is of a saved run.
    run_info = RunInfo(
        started_at=started_at,
        finished_at=datetime.now(UTC),
        register_version=version,
        areas=tuple(areas),
        model=model,
        accepted_file=settings.accepted_findings_file,
    )
    rendered = render_report(build_report(result, run_info))

    with factory.begin() as session:
        lock_index(session)  # waits for an `index` that is saving
        save_sections(session, result.parsed, result.chunked)
        save_extraction(session, result.corpus.extractions, result.corpus.references)
        save_findings(session, result.validation.findings)
        # The index belongs to the sections and findings just replaced (fail closed).
        clear_index(session)
    _log.info(
        "Saved the sections, chunks, facts, references and findings in one transaction, "
        "and emptied the search index: build it again with `index`"
    )

    markdown, json = write_report(rendered, settings.reports_dir)
    _log.info("Report: %s and %s", markdown, json)


def run(areas: Sequence[str], embedder: Embedder) -> None:
    """Steps 1-6 for the same areas: fetch, parse, process, index."""
    fetched = fetch(areas)
    _log.info("Step 1: %d links (%s)", fetched.total(), counts_text(fetched, FetchStatus))
    parsed = parse()
    _log.info("Step 2: %d files (%s)", parsed.total(), counts_text(parsed, ParseStatus))
    process(areas)
    index(embedder)


def require_embedder(settings: Settings) -> Embedder:
    """The configured embedder; `CommandError` without OPENAI_API_KEY."""
    embedder = openai_embedder(settings)
    if embedder is None:
        raise CommandError(
            "OPENAI_API_KEY is not set: the search index needs it to embed the chunks "
            "and the questions"
        )
    return embedder


def index(embedder: Embedder) -> None:
    """Step 6: embed the texts the cache lacks, then replace the search index."""
    settings = get_settings()
    started = time.monotonic()
    factory = session_factory(create_db_engine())
    with factory() as session:
        plan, scopes = index_input(session)
        gone = files_gone_since_checked(session)
        cached = cached_embeddings(
            session, embedder.name, {chunk.text_hash for chunk in plan.chunks}
        )
    planned = {chunk.key.sha256 for chunk in plan.chunks}
    unlinked = sorted(planned - scopes.keys())
    if unlinked:
        raise CommandError(
            f"{len(unlinked)} files with chunks have no link from an agreement page any more "
            f"(e.g. {unlinked[0][:12]}): run `process` before `index`"
        )
    # Step 5 would hold these back; it has not seen their pages go (a `fetch` since).
    unchecked = sorted(planned & gone)
    if unchecked:
        raise CommandError(
            f"{len(unchecked)} files lost their last agreement page after the last `process` "
            f"(e.g. {unchecked[0][:12]}): run `process` before `index`"
        )
    missing = {
        chunk.text_hash: chunk.embedded_text
        for chunk in plan.chunks
        if chunk.text_hash not in cached
    }
    _log.info(
        f"Step 6: {len(plan.chunks):,} chunks to index, {len(cached) + len(missing):,} texts: "
        f"{len(cached):,} from the cache, {len(missing):,} to embed with {embedder.name}"
    )
    embedded = embed_texts(factory, embedder, missing, settings.embedding_batch_size)

    terms = build_term_index([chunk.terms for chunk in plan.chunks])
    with factory.begin() as session:
        lock_index(session)  # a `process` that commits after this waits, then empties the index
        if index_input(session) != (plan, scopes) or files_gone_since_checked(session) & planned:
            raise CommandError(
                "the chunks, the quarantine or the agreement pages changed while the texts "
                "were embedded, so the index was not saved: run `index` again (the "
                "embeddings are cached)"
            )
        save_index(
            session, plan, terms, cached | embedded, scopes, embedder.name, swedish_text.ANALYSER
        )
    _log.info(index_line(plan, len(terms.ids), len(cached), len(embedded), started))


def index_line(plan: IndexPlan, words: int, cached: int, embedded: int, started: float) -> str:
    """Step 6's log line: what was indexed, what was held back, where the embeddings came from."""
    files = len({chunk.key.sha256 for chunk in plan.chunks})
    return (
        f"Step 6: {len(plan.chunks):,} chunks of {files:,} files indexed, "
        f"{plan.held_back:,} held back by the quarantine; {words:,} words; texts from the "
        f"cache {cached:,}, embedded {embedded:,}; {time.monotonic() - started:.0f} s"
    )


def index_input(session: Session) -> tuple[IndexPlan, dict[str, DocumentScope]]:
    """What step 6 indexes: the chunks the quarantine lets through, and each file's scope."""
    plan = plan_index(index_sources(session), quarantine(session))
    scopes = document_scopes(
        catalog_links(session),
        register_entries(session),
        metadata_agreement_numbers(session),
        metadata_document_types(session),
    )
    return plan, scopes


def embed_texts(
    factory: sessionmaker[Session],
    embedder: Embedder,
    texts: Mapping[str, str],
    batch_size: int,
) -> dict[str, list[float]]:
    """Embed the texts (by hash), one request per `batch_size`, each batch cached at once."""
    items = list(texts.items())
    embedded: dict[str, list[float]] = {}
    for number, start in enumerate(range(0, len(items), batch_size), start=1):
        batch = items[start : start + batch_size]
        vectors = embedder.embed_documents([text for _, text in batch])
        new = {text_hash: vector for (text_hash, _), vector in zip(batch, vectors, strict=True)}
        with factory.begin() as session:
            save_cached_embeddings(session, embedder.name, new)
        embedded.update(new)
        if number % _PROGRESS_BATCHES == 0:
            _log.info(f"Step 6: embedded {len(embedded):,} of {len(items):,} texts")
    return embedded


def search(question: str, filters: SearchFilters, limit: int | None, embedder: Embedder) -> None:
    """Print the sections the hybrid search finds for the question; `CommandError` if blank."""
    if not question.strip():
        raise CommandError('the question is blank: ask one, e.g. search "Hur stort är vitet?"')
    settings = get_settings()
    with session_factory(create_db_engine())() as session:
        hits = search_sections(
            session,
            embedder,
            question,
            filters,
            limit or settings.search_limit,
            settings.search_candidates,
            settings.rrf_k,
        )
    for line in hit_lines(hits):
        print(line)


def hit_lines(hits: Sequence[SectionHit]) -> list[str]:
    """The search results as printed: heading, where it is, the best chunk, the copies."""
    if not hits:
        return ["No sections found."]
    lines: list[str] = []
    for place, hit in enumerate(hits, start=1):
        ranks = ", ".join(
            f"{branch} {rank}"
            for branch, rank in (("vector", hit.vector_rank), ("BM25", hit.text_rank))
            if rank is not None
        )
        where = f"{hit.file_title} ({hit.document_type})"
        if hit.page_start is not None:
            where += f", p. {hit.page_start}"
            if hit.page_end not in (None, hit.page_start):
                where += f"-{hit.page_end}"
        snippet = " ".join(hit.snippet.split())
        if len(snippet) > _SNIPPET_CHARS:
            snippet = snippet[:_SNIPPET_CHARS].rstrip() + " ..."

        heading = f"{hit.section_number} {hit.section_title}"
        lines.append(f"{place}. {heading if hit.section_number else hit.section_title}")
        lines.append(f"   score {hit.score:.4f} ({ranks})")
        lines.append(f"   {where}, {hit.sha256[:12]} section {hit.section_position}")
        lines.append(f"   {', '.join(hit.framework_areas)}: {'; '.join(hit.page_titles)}")
        if hit.agreement_numbers:
            lines.append(f"   agreement {', '.join(hit.agreement_numbers)}")
        lines.append(f"   {snippet}")
        for copy in hit.copies:
            number = f" {copy.section_number}" if copy.section_number else ""
            lines.append(
                f"   same text: {copy.file_title}{number}, "
                f"{copy.sha256[:12]} section {copy.section_position}"
            )
    return lines


def check_areas(areas: Iterable[str], register: Sequence[RegisterEntry]) -> None:
    """Raise `UnknownAreaError` unless every area is a framework area of the register.

    The coverage check looks for a document of every agreement of the run's
    areas, so a misspelt area would make it look for none, silently.
    """
    known = sorted({entry.framework_area for entry in register})
    if not known:
        raise UnknownAreaError(
            "the register is empty: load it first (python -m avtalsagent.register --download)"
        )
    missing = [area for area in areas if area not in known]
    if missing:
        raise UnknownAreaError(
            f"not in the register: {', '.join(missing)}. Known areas: {', '.join(known)}"
        )


def load_parsed(
    files: Iterable[SourceFile], parsed_dir: Path
) -> tuple[list[ParsedDocument], list[SourceFile]]:
    """The files step 2 has parsed, from its cache, and the files it has no result for."""
    parsed: list[ParsedDocument] = []
    unparsed: list[SourceFile] = []
    for file in files:
        document = load_cached(parsed_dir, file.sha256)
        if document is None:
            unparsed.append(file)
        else:
            parsed.append(document)
    return parsed, unparsed


def step_lines(result: pipeline.IngestionResult) -> list[str]:
    """One log line each for steps 3, 4 and 5 of a run, with their counts."""
    chunked = result.chunked
    outlines = Counter(document.outline for document in chunked)
    step3 = (
        f"Step 3: {len(chunked)} files, "
        f"{sum(len(document.sections) for document in chunked):,} sections, "
        f"{sum(len(document.chunks) for document in chunked):,} chunks "
        f"({counts_text(outlines, OutlineKind)})"
    )
    if result.unlinked:
        step3 += f"; left out, no page links to them: {len(result.unlinked)}"
    # The pages step 3 leaves out from an e-signature certificate on, so that a rule
    # that took agreement text for a certificate would show here rather than nowhere.
    removed = [
        sum(1 for page in document.pages if page.number >= first)
        for document in result.parsed
        if (first := signature_certificate_page(document.blocks)) is not None
    ]
    if removed:
        step3 += f"; e-signature certificates left out: files {len(removed)}, pages {sum(removed)}"

    corpus = result.corpus
    counted = [reference for reference in corpus.references if counts_in_rate(reference)]
    resolved = sum(1 for reference in counted if reference.status is ReferenceStatus.RESOLVED)
    step4 = (
        f"Step 4: {sum(len(extraction.facts) for extraction in corpus.extractions):,} facts, "
        f"{len(corpus.references):,} references; resolved {resolved:,} of {len(counted):,} "
        f"counted ({resolution_rate(corpus.references):.1%}), "
        f"by the rules alone {corpus.rules_rate:.1%}"
    )
    stats = corpus.match_stats
    if stats is None:
        step4 += "; language model not used"
    else:
        step4 += (
            f"; language model: asked {stats.asked}, answered {stats.answered}, "
            f"calls {stats.calls}, from the cache {stats.cache_hits}"
        )

    validation = result.validation
    open_findings = [finding for finding in validation.findings if finding.accepted_reason is None]
    severities = Counter(finding.severity for finding in open_findings)
    coverage = Counter(item.status for item in validation.coverage)
    step5 = (
        f"Step 5: {len(validation.findings)} findings ({counts_text(severities, Severity)}, "
        f"accepted {len(validation.findings) - len(open_findings)}); quarantine: files "
        f"{len(validation.quarantine.files)}, sections {len(validation.quarantine.sections)}; "
        f"agreements of the run's areas: {len(validation.coverage)} "
        f"({counts_text(coverage, CoverageStatus)})"
    )
    if validation.unused_acceptances:
        step5 += f"; acceptances that match no finding: {len(validation.unused_acceptances)}"
    return [step3, step4, step5]


def counts_text[Kind: StrEnum](counts: Mapping[Kind, int], kinds: Iterable[Kind]) -> str:
    """The counts in the order of `kinds`, zeros included: "quarantine 16, report 5, note 11"."""
    return ", ".join(f"{kind.value} {counts.get(kind, 0):,}" for kind in kinds)


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


def build_parser() -> argparse.ArgumentParser:
    """The commands and their options."""
    parser = argparse.ArgumentParser(description="Ingestion workflow for avropa.se documents.")
    steps = parser.add_subparsers(dest="step", required=True)
    _area_option(steps.add_parser("fetch", help="step 1: download the documents"))
    steps.add_parser("parse", help="step 2: read the downloaded files with Docling")
    _area_option(
        steps.add_parser(
            "process",
            help="steps 3-5: sections and chunks, facts and references, checks against the "
            "register; saves them and writes the report",
        )
    )
    _area_option(steps.add_parser("run", help="steps 1-6: fetch, parse, process and index"))
    steps.add_parser("index", help="step 6: build the search index (embeddings and BM25 weights)")
    search_parser = steps.add_parser(
        "search", help="print the sections the hybrid search finds for a question"
    )
    search_parser.add_argument("question", help='the question, e.g. "Hur stort är vitet?"')
    search_parser.add_argument(
        "--area", dest="framework_area", help="only this framework area, as in the register"
    )
    search_parser.add_argument(
        "--agreement",
        dest="agreement_number",
        help="only this agreement: its supplier card and its procurement's shared files",
    )
    search_parser.add_argument(
        "--type",
        dest="document_type",
        choices=[document_type.value for document_type in DocumentType],
        help="only documents of this type, e.g. general_terms",
    )
    search_parser.add_argument(
        "--limit",
        type=positive_int,
        help="sections to print (at least 1); default SEARCH_LIMIT from the settings",
    )
    outline_parser = steps.add_parser("outline", help="print a file's table of contents")
    outline_parser.add_argument("sha256", nargs="?", help="the file's hash or its beginning")
    steps.add_parser("verify", help="check the sections against each PDF's text layer")
    return parser


def positive_int(value: str) -> int:
    """An argparse type: a whole number of at least 1."""
    try:
        number = int(value)
    except ValueError:
        number = 0
    if number < 1:
        raise argparse.ArgumentTypeError(f"must be a whole number of at least 1, not {value!r}")
    return number


def _area_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--area",
        action="append",
        help="framework area as named in the register (repeatable); default from settings",
    )


def configure_logging(level: str) -> None:
    """Log this package's lines at `level` ("INFO"), other libraries' warnings only.

    The libraries stay at WARNING whatever LOG_LEVEL says: httpx logs every
    request at INFO, which would bury the one line per step among hundreds.
    """
    logging.basicConfig(format=_LOG_FORMAT, level=logging.WARNING)
    logging.getLogger("avtalsagent").setLevel(level.upper())


def main() -> None:
    args = build_parser().parse_args()
    settings = get_settings()
    configure_logging(settings.log_level)
    areas: list[str] = getattr(args, "area", None) or settings.fetch_areas
    if args.step == "fetch":
        fetch(areas)
    elif args.step == "parse":
        parse()
    elif args.step in ("process", "index", "run", "search"):
        try:
            if args.step == "process":
                process(areas)
            elif args.step == "index":
                index(require_embedder(settings))
            elif args.step == "run":
                # Checked first, so a run without a key stops before it fetches anything.
                run(areas, require_embedder(settings))
            else:
                filters = SearchFilters(
                    framework_area=args.framework_area,
                    agreement_number=args.agreement_number,
                    document_type=args.document_type,
                )
                search(args.question, filters, args.limit, require_embedder(settings))
        except (LanguageModelError, EmbeddingError, IndexNotReadyError, CommandError) as error:
            # The messages name the error type, the model or the cache, never the key.
            _log.error("%s: %s", args.step, error)
            raise SystemExit(1) from None
    elif args.step == "outline":
        outline(args.sha256)
    elif args.step == "verify":
        verify()


if __name__ == "__main__":
    main()
