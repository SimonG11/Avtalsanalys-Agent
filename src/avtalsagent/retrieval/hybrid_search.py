"""Hybrid search: the sections that best match a question, by embedding and by BM25.

What:
    `search` answers a question with the best sections: for each, where it is
    (file, number, title, pages, scope), the text of its best chunk and the
    other sections in scope with the same text. `vector_candidates` and
    `text_candidates` are its two branches: the chunks nearest the question's
    embedding, and the chunks with the highest BM25 score for its words.
    `IndexNotReadyError` is raised when there is no index, or one built with
    another embedding model or text analyser than the search's.

Why:
    An embedding finds a clause written in other words than the question's;
    BM25 finds the rare exact words ("uppsägningstid", a product name) that an
    embedding blurs. Reciprocal rank fusion combines the two by rank, so their
    scores, which are not comparable, need no weighting (ADR 0011). The agent
    reads and cites sections, so the result is sections, not chunks. A text
    repeated in several documents (general terms printed again in a
    procurement document) is one result with its copies listed, so the copies
    do not crowd out other results and the agent can still cite the right
    agreement's document.

How:
    Each branch is one SQL query with the filters in its WHERE clause, so
    they filter before ranking, and an exact search ranks every chunk in
    scope. The vector branch orders by `embedding <#> :query_vector`,
    pgvector's negative inner product, which on unit vectors is minus the
    cosine similarity. The BM25 branch orders by `term_weights <#>
    :query_terms`, where the query is a sparse vector with 1 for each of the
    question's stems found in `search_term`, so the product is minus the
    chunk's BM25 score; chunks without any of the words (score 0) are left
    out. Ties are broken by the chunk key, so a search always gives the same
    order, and the offline measurement (`evals/offline.py`) the same ranking.
    `retrieval/fusion.py` turns each branch's chunks into sections and fuses
    them; two more queries read the details of the top sections and their
    copies. A framework area, procurement or document type filter keeps the
    files whose scope (`document_scope`) has it. An agreement filter keeps the agreement's own
    files (its supplier card) and the files of its procurement that belong to
    no supplier (general terms, requirements), but not other suppliers'
    cards; the procurement is the register's (`agreement`).
"""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from pgvector.sparsevec import SparseVector
from pgvector.sqlalchemy import SPARSEVEC, Vector
from sqlalchemy import ColumnElement, String, and_, any_, bindparam, func, or_, select, tuple_
from sqlalchemy.orm import Session

from avtalsagent.db import models
from avtalsagent.domain.search import ChunkKey, FusedSection, RankedChunk, SearchFilters
from avtalsagent.ingestion.index_store import current_build
from avtalsagent.retrieval import fusion, swedish_text
from avtalsagent.retrieval.embedder import Embedder

_BUILD_COMMAND = "python -m avtalsagent.ingestion index"
_NO_FILTERS = SearchFilters()  # frozen, so one instance serves as the default


class IndexNotReadyError(RuntimeError):
    """There is no search index, or it was built with another model or analyser."""


@dataclass(frozen=True)
class SectionCopy:
    """Another section, in the search's scope, with the same text as a hit."""

    sha256: str
    section_position: int
    section_number: str | None
    file_title: str


@dataclass(frozen=True)
class SectionHit:
    """One result: a section, shown by its best chunk."""

    sha256: str
    section_position: int
    section_number: str | None  # e.g. "6.21.4"; None for a section without a number
    section_title: str
    path: tuple[str, ...]  # the headings from the top down to this one
    page_start: int | None
    page_end: int | None
    file_title: str  # the link text used most often, e.g. "Allmänna villkor"
    document_type: str  # `DocumentType` value, e.g. "general_terms"
    framework_areas: tuple[str, ...]
    page_titles: tuple[str, ...]
    agreement_numbers: tuple[str, ...]  # a supplier card's; empty for shared files
    snippet: str  # the best chunk's text
    score: float  # reciprocal rank fusion
    vector_rank: int | None  # the section's 1-based place in each branch; None if absent
    text_rank: int | None
    copies: tuple[SectionCopy, ...]  # other sections in scope with the same text


def vector_candidates(
    session: Session, query_vector: Sequence[float], filters: SearchFilters, n: int
) -> list[RankedChunk]:
    """The `n` chunks in scope nearest the question's embedding, best first."""
    chunk = models.SearchChunk
    query = bindparam("query_vector", list(query_vector), type_=Vector())
    rows = session.execute(
        select(chunk.sha256, chunk.section_position, chunk.position, chunk.section_hash)
        .join(models.DocumentScope, models.DocumentScope.sha256 == chunk.sha256)
        .where(*scope_conditions(filters))
        .order_by(
            chunk.embedding.max_inner_product(query),
            chunk.sha256,
            chunk.section_position,
            chunk.position,
        )
        .limit(n)
    )
    return [_ranked(*row) for row in rows]


def text_candidates(
    session: Session, query_terms: Sequence[str], filters: SearchFilters, n: int
) -> list[RankedChunk]:
    """The `n` chunks in scope with the highest BM25 score for the question's stems, best first.

    Only chunks with at least one of the stems are returned; a question none
    of whose stems is in the index finds none.
    """
    if not query_terms:
        return []
    ids = session.scalars(
        select(models.SearchTerm.id).where(models.SearchTerm.term.in_(list(query_terms)))
    ).all()
    if not ids:
        return []
    dimensions = session.scalar(select(models.IndexBuild.term_count))
    if dimensions is None:
        raise IndexNotReadyError(f"there is no search index: build it with `{_BUILD_COMMAND}`")
    chunk = models.SearchChunk
    query = bindparam(
        "query_terms", SparseVector(dict.fromkeys(ids, 1.0), dimensions), type_=SPARSEVEC()
    )
    minus_score = chunk.term_weights.max_inner_product(query)
    rows = session.execute(
        select(chunk.sha256, chunk.section_position, chunk.position, chunk.section_hash)
        .join(models.DocumentScope, models.DocumentScope.sha256 == chunk.sha256)
        .where(minus_score < 0, *scope_conditions(filters))
        .order_by(minus_score, chunk.sha256, chunk.section_position, chunk.position)
        .limit(n)
    )
    return [_ranked(*row) for row in rows]


def search(
    session: Session,
    embedder: Embedder,
    query: str,
    filters: SearchFilters = _NO_FILTERS,
    limit: int = 8,
    candidates: int = 100,
    rrf_k: int = 60,
) -> list[SectionHit]:
    """The `limit` best sections for the question, by reciprocal rank fusion of both branches.

    Each branch passes its best `candidates` chunks to the fusion. Raises
    `IndexNotReadyError` before the question is embedded when the index is
    missing or was built with another embedder or analyser.
    """
    check_index(session, embedder.name)
    vector = vector_candidates(session, embedder.embed_query(query), filters, candidates)
    text = text_candidates(session, swedish_text.query_terms(query), filters, candidates)
    fused = fusion.fuse(vector, text, rrf_k)[:limit]
    if not fused:
        return []
    return _hits(session, fused, _copies(session, fused, filters))


def check_index(session: Session, embedding_model: str) -> None:
    """Raise `IndexNotReadyError` unless the index was built for this embedder and analyser.

    A question embedded by another model, or analysed into other stems, than
    the chunks were would be compared with vectors and words it does not
    match, and the search would return wrong sections without an error.
    """
    build = current_build(session)
    if build is None:
        raise IndexNotReadyError(f"there is no search index: build it with `{_BUILD_COMMAND}`")
    if build.embedding_model != embedding_model:
        raise IndexNotReadyError(
            f"the search index was built with the embedding model {build.embedding_model}, "
            f"not {embedding_model}: build it again with `{_BUILD_COMMAND}`"
        )
    if build.analyser != swedish_text.ANALYSER:
        raise IndexNotReadyError(
            f"the search index was built with the text analyser {build.analyser}, "
            f"not {swedish_text.ANALYSER}: build it again with `{_BUILD_COMMAND}`"
        )


def scope_conditions(filters: SearchFilters) -> list[ColumnElement[bool]]:
    """The WHERE conditions of the filters on `document_scope`; none without filters."""
    scope = models.DocumentScope
    conditions: list[ColumnElement[bool]] = []
    if filters.framework_area is not None:
        area = bindparam("framework_area", filters.framework_area, String())
        conditions.append(area == any_(scope.framework_areas))
    if filters.procurement_number is not None:
        procurement = bindparam("procurement_number", filters.procurement_number, String())
        conditions.append(procurement == any_(scope.procurement_numbers))
    if filters.agreement_number is not None:
        agreement = bindparam("agreement_number", filters.agreement_number, String())
        # The agreement's procurement in the register; NULL for an unknown agreement,
        # which then matches no shared file.
        its_procurement = (
            select(models.Agreement.procurement_number)
            .where(models.Agreement.agreement_number == agreement)
            .scalar_subquery()
        )
        conditions.append(
            or_(
                agreement == any_(scope.agreement_numbers),
                and_(
                    func.cardinality(scope.agreement_numbers) == 0,
                    its_procurement == any_(scope.procurement_numbers),
                ),
            )
        )
    if filters.document_type is not None:
        conditions.append(scope.document_type == filters.document_type)
    return conditions


def _ranked(sha256: str, section_position: int, position: int, section_hash: str) -> RankedChunk:
    return RankedChunk(ChunkKey(sha256, section_position, position), section_hash)


def _copies(
    session: Session, fused: Sequence[FusedSection], filters: SearchFilters
) -> dict[str, tuple[SectionCopy, ...]]:
    """By section hash: the indexed sections in scope with that hash, but the one shown."""
    shown = {section.section_hash: section.key for section in fused}
    chunk, section = models.SearchChunk, models.DocumentSection
    rows = session.execute(
        select(
            chunk.section_hash,
            chunk.sha256,
            chunk.section_position,
            section.number,
            models.DocumentMetadata.title,
        )
        .distinct()
        .join(models.DocumentScope, models.DocumentScope.sha256 == chunk.sha256)
        .join(
            section,
            (section.sha256 == chunk.sha256) & (section.position == chunk.section_position),
        )
        .join(models.DocumentMetadata, models.DocumentMetadata.sha256 == chunk.sha256)
        .where(chunk.section_hash.in_(list(shown)), *scope_conditions(filters))
        .order_by(chunk.section_hash, chunk.sha256, chunk.section_position)
    )
    copies: defaultdict[str, list[SectionCopy]] = defaultdict(list)
    for section_hash, sha256, section_position, number, file_title in rows:
        key = shown[section_hash]
        if (sha256, section_position) != (key.sha256, key.section_position):
            copies[section_hash].append(SectionCopy(sha256, section_position, number, file_title))
    return {section_hash: tuple(items) for section_hash, items in copies.items()}


def _hits(
    session: Session, fused: Sequence[FusedSection], copies: Mapping[str, tuple[SectionCopy, ...]]
) -> list[SectionHit]:
    """The fused sections as results: each best chunk's section, file and scope, in one query."""
    chunk, section = models.SectionChunk, models.DocumentSection
    metadata, scope = models.DocumentMetadata, models.DocumentScope
    rows = session.execute(
        select(
            chunk.sha256,
            chunk.section_position,
            chunk.position,
            chunk.text,
            section.number,
            section.title,
            section.path,
            section.page_start,
            section.page_end,
            metadata.title.label("file_title"),
            metadata.document_type,
            scope.framework_areas,
            scope.page_titles,
            scope.agreement_numbers,
        )
        .join(
            section,
            (section.sha256 == chunk.sha256) & (section.position == chunk.section_position),
        )
        .join(metadata, metadata.sha256 == chunk.sha256)
        .join(scope, scope.sha256 == chunk.sha256)
        .where(
            tuple_(chunk.sha256, chunk.section_position, chunk.position).in_(
                [(item.key.sha256, item.key.section_position, item.key.position) for item in fused]
            )
        )
    )
    by_key = {ChunkKey(row.sha256, row.section_position, row.position): row for row in rows}
    hits: list[SectionHit] = []
    for item in fused:
        row = by_key[item.key]
        hits.append(
            SectionHit(
                sha256=row.sha256,
                section_position=row.section_position,
                section_number=row.number,
                section_title=row.title,
                path=tuple(row.path),
                page_start=row.page_start,
                page_end=row.page_end,
                file_title=row.file_title,
                document_type=row.document_type,
                framework_areas=tuple(row.framework_areas),
                page_titles=tuple(row.page_titles),
                agreement_numbers=tuple(row.agreement_numbers),
                snippet=row.text,
                score=item.score,
                vector_rank=item.vector_rank,
                text_rank=item.text_rank,
                copies=copies.get(item.section_hash, ()),
            )
        )
    return hits
