"""The search index and its results, as plain values (M5).

What:
    `ChunkKey`: which chunk, by file, section and position. `DocumentScope`:
    where a file belongs (framework areas, procurements, agreements, pages).
    `IndexedChunk`: one chunk as step 6 indexes it: the text that is embedded,
    the hash that finds its embedding in the cache, the hash of its section's
    text and the stems of the BM25 index. `IndexPlan`: every chunk to index and
    how many the quarantine held back. `SearchFilters`: what a search can be
    limited to. `RankedChunk`: a chunk in one branch's ranking, and
    `FusedSection`: one result of the fusion of both branches.

Why:
    Step 6 (`ingestion/step6_index.py`), the database code
    (`ingestion/index_store.py`, `retrieval/hybrid_search.py`) and the offline
    measurement (`evals/`) pass these between them. Plain values with no
    database let the same functions build the index in production and in the
    measurement, so the measured numbers are those of the code that runs.

How:
    Frozen dataclasses. A section is found by `(sha256, section_position)`,
    a chunk by `ChunkKey`; both are the primary keys of the stored tables. The
    `section_hash` groups copies: a section whose text is identical to another
    (general terms repeated as a chapter of a procurement document) shares its
    hash, and the search returns the group once.
"""

from dataclasses import dataclass


@dataclass(frozen=True, order=True)
class ChunkKey:
    """A chunk's key in `section_chunk` and `search_chunk`; ordering breaks score ties."""

    sha256: str
    section_position: int
    position: int


@dataclass(frozen=True)
class DocumentScope:
    """Where a file belongs, from the agreement pages that link to it and the register."""

    sha256: str
    framework_areas: tuple[str, ...]  # sorted, as the register spells them
    procurement_numbers: tuple[str, ...]  # sorted
    agreement_numbers: tuple[str, ...]  # sorted; a supplier card's, empty for shared files
    page_titles: tuple[str, ...]  # sorted, e.g. "IT-drift Större, fler än 200 anställda"
    document_type: str | None = None  # `DocumentType` value from step 4, e.g. "general_terms"


@dataclass(frozen=True)
class IndexedChunk:
    """One chunk as step 6 indexes it."""

    key: ChunkKey
    embedded_text: str  # context header + blank line + chunk text
    text_hash: str  # sha256 of embedded_text: the embedding cache's key
    section_hash: str  # sha256 of the section's normalised text: groups copies
    terms: tuple[str, ...]  # stems of embedded_text, in order (`retrieval/swedish_text.py`)


@dataclass(frozen=True)
class IndexPlan:
    """Every chunk to index, in key order, and how many the quarantine left out."""

    chunks: tuple[IndexedChunk, ...]
    held_back: int  # chunks of held-back files or sections


@dataclass(frozen=True)
class SearchFilters:
    """What a search is limited to; None is no limit.

    An agreement number limits to that supplier's card and the files of its
    procurement that belong to no supplier (general terms, requirements). A
    document type is a `DocumentType` value ("general_terms").
    """

    framework_area: str | None = None
    procurement_number: str | None = None
    agreement_number: str | None = None
    document_type: str | None = None


@dataclass(frozen=True)
class RankedChunk:
    """A chunk in one branch's ranking (vector or BM25), best first."""

    key: ChunkKey
    section_hash: str


@dataclass(frozen=True)
class FusedSection:
    """One result of the fusion: a section, or a group of identical copies of one.

    `key` is the best-ranked chunk of the group, whose section is the one shown;
    the ranks are the group's 1-based places in each branch, None where absent.
    """

    section_hash: str
    score: float  # reciprocal rank fusion
    key: ChunkKey
    vector_rank: int | None
    text_rank: int | None
