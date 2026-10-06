"""The database side of step 6: the chunks it indexes, the embedding cache and the index.

What:
    `index_sources` reads every stored chunk with its section's text, and
    `metadata_agreement_numbers` and `metadata_document_types` each file's
    agreement number and document type from step 4, and
    `files_gone_since_checked` the files whose agreement pages have all gone
    since step 5 last checked them.
    `cached_embeddings` and `save_cached_embeddings` read and add to the
    embedding cache. `save_index` replaces the search index: the chunks with
    their embeddings and BM25 weights, the words of the BM25 index, the scope
    of each indexed file and the row that records how the index was built,
    which `current_build` reads back. `clear_index` empties the index, and
    `lock_index` makes its two writers, `process` and `index`, wait for each
    other.

Why:
    Step 6 (`step6_index.py`) and the BM25 code (`retrieval/bm25.py`) work on
    plain values, so they can be tested and measured without a database. This
    module is the only place that writes the search tables, as
    `section_store.py` and `extraction_store.py` are for steps 2-5. The
    search (`retrieval/hybrid_search.py`) reads the build row to refuse an
    index made with another embedding model or text analyser than its own.

How:
    SQLAlchemy statements on the tables in `db/models.py`, with pgvector's
    types: an embedding is sent as a `vector`, a chunk's BM25 weights as a
    `sparsevec` whose dimension is the number of words in the index (word
    `id` is its position). `save_index` deletes the whole index and inserts
    the new one, so the `index` command calls it in one transaction and no
    query sees half an index. A search is several queries, though, and one
    that runs during a rebuild can mix the old index and the new one
    (docs/steg/05-sokning.md, known limitations). Rows are sent as lists
    (executemany), which SQLAlchemy sends in batches under Postgres' limit of
    65,535 parameters. The cache is keyed by the embedder's name (model and
    dimensions) and the text's hash and has no foreign key, so it outlives
    every rebuild; adding a vector it already has does nothing. `process`
    calls `clear_index` in the transaction that replaces the sections: the
    chunk rows and scopes go with their chunks and files anyway (ON DELETE
    CASCADE), and without the build row the search refuses to run until
    `index` has built the index again. Both commands take a transaction-level
    advisory lock first in the transaction that writes, so a `process` that
    commits while `index` embeds is seen by `index`'s final check, and one
    that starts later empties the new index.
"""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pgvector.sparsevec import SparseVector
from sqlalchemy import delete, func, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from avtalsagent.db import models
from avtalsagent.domain.search import ChunkKey, DocumentScope, IndexPlan
from avtalsagent.ingestion.checks import still_published
from avtalsagent.ingestion.step6_index import IndexSource
from avtalsagent.retrieval.bm25 import TermIndex, document_weights

# The key of the advisory lock `lock_index` takes; any fixed number shared by the writers.
_INDEX_LOCK = 20261007

# Text hashes per query when reading the cache: an IN list of 13,000 would be 13,000
# parameters, and 150,000 chunks would pass Postgres' limit.
_CACHE_LOOKUP_BATCH = 1000


@dataclass(frozen=True)
class IndexBuildInfo:
    """How the current search index was built (the `index_build` row)."""

    embedding_model: str  # the embedder's name, e.g. "text-embedding-3-large:1536"
    analyser: str  # `swedish_text.ANALYSER`, e.g. "sv-1 snowballstemmer 3.1.1"
    term_count: int  # words in the BM25 index: the dimension of term_weights
    chunk_count: int
    held_back_count: int  # chunks the quarantine left out
    document_count: int  # files with at least one indexed chunk
    built_at: datetime


def index_sources(session: Session) -> list[IndexSource]:
    """Every stored chunk with its context header and its section's text, in key order."""
    chunk, section = models.SectionChunk, models.DocumentSection
    rows = session.execute(
        select(
            chunk.sha256,
            chunk.section_position,
            chunk.position,
            chunk.context_header,
            chunk.text,
            section.text,
            section.number,
        )
        .join(
            section,
            (section.sha256 == chunk.sha256) & (section.position == chunk.section_position),
        )
        .order_by(chunk.sha256, chunk.section_position, chunk.position)
    )
    return [
        IndexSource(
            key=ChunkKey(sha256, section_position, position),
            context_header=context_header,
            text=text,
            section_text=section_text,
            section_number=number,
        )
        for sha256, section_position, position, context_header, text, section_text, number in rows
    ]


def metadata_agreement_numbers(session: Session) -> dict[str, str | None]:
    """Each checked file's agreement number from step 4; None for a file of no supplier."""
    rows = session.execute(
        select(models.DocumentMetadata.sha256, models.DocumentMetadata.agreement_number)
    )
    return {sha256: number for sha256, number in rows}


def files_gone_since_checked(session: Session) -> set[str]:
    """Files whose agreement pages are all gone from avropa.se but that step 5 has not judged.

    Step 5 holds back a file whose every page is missing, with a
    `still_published` finding. A file with all its pages missing and no such
    finding lost its last page in a `fetch` after the last `process`.
    """
    page = models.AgreementPage
    link = models.AgreementPageDocument
    source = models.SourceDocument
    gone = (
        select(source.sha256)
        .join(link, link.document_url == source.url)
        .join(page, page.url == link.page_url)
        .group_by(source.sha256)
        .having(func.bool_and(page.missing_since.is_not(None)))
    )
    judged = select(models.ValidationFinding.sha256).where(
        models.ValidationFinding.check_name == still_published.CHECK,
        models.ValidationFinding.sha256.is_not(None),
    )
    return set(session.scalars(gone.except_(judged)))


def metadata_document_types(session: Session) -> dict[str, str]:
    """Each checked file's document type from step 4, e.g. "general_terms"."""
    rows = session.execute(
        select(models.DocumentMetadata.sha256, models.DocumentMetadata.document_type)
    )
    return {sha256: document_type for sha256, document_type in rows}


def cached_embeddings(
    session: Session, model: str, hashes: Collection[str]
) -> dict[str, list[float]]:
    """The cached embeddings of `model` for the given text hashes; missing ones are left out."""
    cache = models.EmbeddingCache
    wanted = sorted(set(hashes))
    found: dict[str, list[float]] = {}
    for start in range(0, len(wanted), _CACHE_LOOKUP_BATCH):
        batch = wanted[start : start + _CACHE_LOOKUP_BATCH]
        rows = session.execute(
            select(cache.text_hash, cache.embedding).where(
                cache.model == model, cache.text_hash.in_(batch)
            )
        )
        found.update({text_hash: list(embedding) for text_hash, embedding in rows})
    return found


def save_cached_embeddings(
    session: Session, model: str, embeddings: Mapping[str, Sequence[float]]
) -> None:
    """Add embeddings of `model` to the cache, by text hash; one it already has is kept."""
    rows = [
        {"model": model, "text_hash": text_hash, "embedding": list(embedding)}
        for text_hash, embedding in embeddings.items()
    ]
    if rows:
        session.execute(pg_insert(models.EmbeddingCache).on_conflict_do_nothing(), rows)


def save_index(
    session: Session,
    plan: IndexPlan,
    terms: TermIndex,
    embeddings: Mapping[str, Sequence[float]],
    scopes: Mapping[str, DocumentScope],
    embedding_model: str,
    analyser: str,
) -> None:
    """Replace the search index with the chunks of `plan`.

    `terms` is the BM25 index of the plan's chunks (`bm25.build_term_index`),
    `embeddings` the embedding of each chunk's text by its hash, and
    `scopes` the scope of each file, of which only files with an indexed
    chunk are stored. Raises ValueError, before anything is deleted, when a
    chunk has no embedding or a file no scope, or `terms` is of other chunks.
    """
    files = sorted({chunk.key.sha256 for chunk in plan.chunks})
    unembedded = [chunk.key for chunk in plan.chunks if chunk.text_hash not in embeddings]
    if unembedded:
        raise ValueError(f"{len(unembedded)} chunks have no embedding, e.g. {unembedded[0]}")
    unscoped = [sha256 for sha256 in files if sha256 not in scopes]
    if unscoped:
        raise ValueError(f"{len(unscoped)} indexed files have no scope, e.g. {unscoped[0]}")
    if terms.chunk_total != len(plan.chunks):
        raise ValueError(
            f"the word index counts {terms.chunk_total} chunks, the plan has {len(plan.chunks)}"
        )

    clear_index(session)
    dimensions = len(terms.ids)
    scope_rows = [_scope_row(scopes[sha256]) for sha256 in files]
    term_rows = [
        {"term": term, "id": number, "chunk_count": terms.chunk_counts[term]}
        for term, number in terms.ids.items()
    ]
    chunk_rows = [
        {
            "sha256": chunk.key.sha256,
            "section_position": chunk.key.section_position,
            "position": chunk.key.position,
            "section_hash": chunk.section_hash,
            "embedding": list(embeddings[chunk.text_hash]),
            "term_weights": SparseVector(document_weights(terms, chunk.terms), dimensions),
        }
        for chunk in plan.chunks
    ]
    for model, rows in (
        (models.DocumentScope, scope_rows),
        (models.SearchTerm, term_rows),
        (models.SearchChunk, chunk_rows),
    ):
        if rows:
            session.execute(insert(model), rows)
    session.execute(
        insert(models.IndexBuild).values(
            id=1,
            embedding_model=embedding_model,
            analyser=analyser,
            term_count=dimensions,
            chunk_count=len(plan.chunks),
            held_back_count=plan.held_back,
            document_count=len(files),
        )
    )


def lock_index(session: Session) -> None:
    """Wait until no other `process` or `index` writes, then hold that off until commit."""
    session.execute(select(func.pg_advisory_xact_lock(_INDEX_LOCK)))


def clear_index(session: Session) -> None:
    """Delete the search index: the build row, the words, the chunks and the scopes."""
    for model in (models.IndexBuild, models.SearchTerm, models.SearchChunk, models.DocumentScope):
        session.execute(delete(model))


def current_build(session: Session) -> IndexBuildInfo | None:
    """How the current index was built; None when there is none."""
    row = session.scalar(select(models.IndexBuild).where(models.IndexBuild.id == 1))
    if row is None:
        return None
    return IndexBuildInfo(
        embedding_model=row.embedding_model,
        analyser=row.analyser,
        term_count=row.term_count,
        chunk_count=row.chunk_count,
        held_back_count=row.held_back_count,
        document_count=row.document_count,
        built_at=row.built_at,
    )


def _scope_row(scope: DocumentScope) -> dict[str, Any]:
    return {
        "sha256": scope.sha256,
        "framework_areas": list(scope.framework_areas),
        "procurement_numbers": list(scope.procurement_numbers),
        "agreement_numbers": list(scope.agreement_numbers),
        "page_titles": list(scope.page_titles),
        "document_type": scope.document_type,
    }
