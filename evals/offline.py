"""The hybrid search in memory, for measuring it without a database.

What:
    `OfflineIndex.build` holds every indexed chunk's embedding, BM25 weights
    and file scope in numpy arrays. `vector_candidates` and `text_candidates`
    are the two branches of `retrieval/hybrid_search.py`, `search` fuses them
    as the production search does.

Why:
    Measuring the search means running the test questions against many
    variants of the index (chunk size, embedding model, analyser). Building
    each in PostgreSQL would take minutes; in memory it takes seconds. The
    numbers only mean something if this is the production search, so it is
    built from the same `IndexPlan`, the same BM25 weights and the same
    fusion, and it ranks and filters exactly as the SQL does; an integration
    test compares the two on real chunks.

How:
    Vector branch: the inner product of the question's vector with every
    chunk's, in float32 as pgvector computes it, best first, ties by chunk
    key (`ORDER BY embedding <#> q, sha256, section_position, position`).
    pgvector and numpy add the products in different orders, so two scores
    closer than about 1e-6 can swap places; equal vectors stay tied, since
    every row is computed the same way (`numpy.einsum`, not BLAS).
    BM25 branch: the sum of the chunk's weights for the question's stems,
    added in stem id order in float32 exactly as pgvector's sparse inner
    product does, so the scores are bitwise those of `term_weights <#> q`.
    Only chunks with a positive score count. Filters are applied before
    ranking, as the SQL's WHERE: an area or procurement must be among the
    file's; an agreement matches its supplier card's files and the files of
    its procurement that belong to no supplier (general terms, requirements);
    a document type must be the file's.
    Every indexed file must have a scope, as `index_store.save_index` requires.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from avtalsagent.domain.search import (
    ChunkKey,
    DocumentScope,
    FusedSection,
    IndexPlan,
    RankedChunk,
    SearchFilters,
)
from avtalsagent.retrieval import bm25
from avtalsagent.retrieval.fusion import fuse

# How far a stored embedding's length may be from 1: they are normalised in float64
# and stored in float32, which leaves about 1e-7.
_NORM_TOLERANCE = 1e-3


@dataclass(frozen=True, eq=False)
class SparseWeights:
    """The BM25 weights as an n x term_count float32 matrix, stored by column.

    Column t, the chunks that contain stem t, is rows[starts[t]:starts[t + 1]]
    (ascending) with the weights values[starts[t]:starts[t + 1]], so a question
    reads only its own stems' columns.
    """

    starts: npt.NDArray[np.int64]  # term_count + 1 offsets
    rows: npt.NDArray[np.int64]
    values: npt.NDArray[np.float32]

    @classmethod
    def from_rows(cls, rows: Sequence[Mapping[int, float]], term_count: int) -> "SparseWeights":
        """The matrix whose row i has the weights `rows[i]` (stem id -> weight)."""
        row_of_entry = np.fromiter(
            (row for row, weights in enumerate(rows) for _ in weights), dtype=np.int64
        )
        column_of_entry = np.fromiter(
            (term_id for weights in rows for term_id in weights), dtype=np.int64
        )
        # Rounded to float32 as pgvector stores them.
        value_of_entry = np.fromiter(
            (weight for weights in rows for weight in weights.values()), dtype=np.float32
        )
        order = np.lexsort((row_of_entry, column_of_entry))
        starts = np.zeros(term_count + 1, dtype=np.int64)
        starts[1:] = np.cumsum(np.bincount(column_of_entry, minlength=term_count))
        return cls(starts, row_of_entry[order], value_of_entry[order])

    def scores(self, term_ids: Iterable[int], row_count: int) -> npt.NDArray[np.float32]:
        """Each row's sum of its weights in the columns `term_ids` (each counted once)."""
        scores = np.zeros(row_count, dtype=np.float32)
        # In ascending id order, as pgvector walks both sparse vectors' indices.
        for term_id in sorted(set(term_ids)):
            start, end = self.starts[term_id], self.starts[term_id + 1]
            scores[self.rows[start:end]] += self.values[start:end]
        return scores


@dataclass(frozen=True, eq=False)
class OfflineIndex:
    """Every indexed chunk, one row each in key order, with what the search needs."""

    keys: tuple[ChunkKey, ...]
    section_hashes: tuple[str, ...]
    embeddings: npt.NDArray[np.float32]  # n x d, rows L2-normalised
    weights: SparseWeights  # n x term_count
    term_ids: dict[str, int]
    scopes: tuple[DocumentScope, ...]  # per row
    agreement_procurements: dict[str, str]  # agreement number -> procurement number

    @classmethod
    def build(
        cls,
        plan: IndexPlan,
        embeddings: Mapping[str, Sequence[float]],
        scopes: Mapping[str, DocumentScope],
        agreement_procurements: Mapping[str, str],
    ) -> "OfflineIndex":
        """The index of `plan`'s chunks.

        `embeddings` are by `IndexedChunk.text_hash` (as in `embedding_cache`) and
        must be L2-normalised; `scopes` are by file sha256, one for every file.
        """
        chunks = sorted(plan.chunks, key=lambda chunk: chunk.key)
        keys = tuple(chunk.key for chunk in chunks)
        if len(set(keys)) != len(keys):
            raise ValueError("the plan has a chunk key twice")
        missing = {chunk.text_hash for chunk in chunks} - embeddings.keys()
        if missing:
            raise KeyError(f"{len(missing)} of the plan's texts have no embedding")
        unscoped = sorted({key.sha256 for key in keys} - scopes.keys())
        if unscoped:
            raise ValueError(f"{len(unscoped)} indexed files have no scope, e.g. {unscoped[0]}")
        if chunks:
            matrix = np.array([embeddings[chunk.text_hash] for chunk in chunks], dtype=np.float32)
            norms = np.linalg.norm(matrix, axis=1)
            if np.any(np.abs(norms - 1) > _NORM_TOLERANCE):
                raise ValueError("the embeddings must be L2-normalised (retrieval.embedder)")
        else:
            matrix = np.zeros((0, 0), dtype=np.float32)
        term_index = bm25.build_term_index([chunk.terms for chunk in chunks])
        weights = SparseWeights.from_rows(
            [bm25.document_weights(term_index, chunk.terms) for chunk in chunks],
            len(term_index.ids),
        )
        return cls(
            keys=keys,
            section_hashes=tuple(chunk.section_hash for chunk in chunks),
            embeddings=matrix,
            weights=weights,
            term_ids=term_index.ids,
            scopes=tuple(scopes[key.sha256] for key in keys),
            agreement_procurements=dict(agreement_procurements),
        )

    def vector_candidates(
        self, query_vector: Sequence[float], filters: SearchFilters, n: int
    ) -> list[RankedChunk]:
        """The `n` chunks in scope whose embeddings are nearest the question's, best first."""
        if not self.keys:
            return []
        query = np.asarray(query_vector, dtype=np.float32)
        if query.shape != (self.embeddings.shape[1],):
            raise ValueError(
                f"the question's vector has {query.size} dimensions,"
                f" the index's {self.embeddings.shape[1]}"
            )
        # einsum computes every row the same way; `@` hands blocks of rows to BLAS kernels
        # that can round identical rows differently, which would split their tie.
        scores = np.einsum("ij,j->i", self.embeddings, query)
        return self._ranked(scores, filters, n, positive_only=False)

    def text_candidates(
        self, query_terms: Sequence[str], filters: SearchFilters, n: int
    ) -> list[RankedChunk]:
        """The `n` chunks in scope with the highest BM25 score for the stems, best first.

        Stems that are in no chunk are ignored; a chunk with none of the
        others is not a candidate.
        """
        term_ids = [self.term_ids[term] for term in query_terms if term in self.term_ids]
        if not term_ids:
            return []
        scores = self.weights.scores(term_ids, len(self.keys))
        return self._ranked(scores, filters, n, positive_only=True)

    def search(
        self,
        query_vector: Sequence[float],
        query_terms: Sequence[str],
        filters: SearchFilters,
        limit: int,
        candidates: int,
        rrf_k: int,
    ) -> list[FusedSection]:
        """The `limit` best section groups of both branches' `candidates`, fused."""
        vector = self.vector_candidates(query_vector, filters, candidates)
        text = self.text_candidates(query_terms, filters, candidates)
        return fuse(vector, text, rrf_k)[:limit]

    def in_scope(self, scope: DocumentScope, filters: SearchFilters) -> bool:
        """Whether a file with `scope` passes `filters` (all that are set)."""
        if filters.framework_area is not None and (
            filters.framework_area not in scope.framework_areas
        ):
            return False
        if filters.procurement_number is not None and (
            filters.procurement_number not in scope.procurement_numbers
        ):
            return False
        if filters.agreement_number is not None:
            own_card = filters.agreement_number in scope.agreement_numbers
            procurement = self.agreement_procurements.get(filters.agreement_number)
            procurement_wide = (
                not scope.agreement_numbers
                and procurement is not None
                and procurement in scope.procurement_numbers
            )
            if not (own_card or procurement_wide):
                return False
        return filters.document_type is None or filters.document_type == scope.document_type

    def _ranked(
        self,
        scores: npt.NDArray[np.float32],
        filters: SearchFilters,
        n: int,
        positive_only: bool,
    ) -> list[RankedChunk]:
        if n < 0:
            raise ValueError(f"cannot return {n} candidates")
        rows = np.flatnonzero(self._filter_mask(filters))
        if positive_only:
            rows = rows[scores[rows] > 0]
        # Highest score first; rows are in key order, so a tie goes to the lower key.
        order = np.lexsort((rows, -scores[rows]))[:n]
        return [RankedChunk(self.keys[row], self.section_hashes[row]) for row in rows[order]]

    def _filter_mask(self, filters: SearchFilters) -> npt.NDArray[np.bool_]:
        if filters == SearchFilters():
            return np.ones(len(self.keys), dtype=np.bool_)
        files = {scope.sha256: scope for scope in self.scopes}
        passes = {sha256: self.in_scope(scope, filters) for sha256, scope in files.items()}
        return np.fromiter(
            (passes[key.sha256] for key in self.keys), dtype=np.bool_, count=len(self.keys)
        )
