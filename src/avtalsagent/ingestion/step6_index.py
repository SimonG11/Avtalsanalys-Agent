"""Ingestion step 6: what the search index holds, on plain values.

What:
    `plan_index` turns the stored chunks into an `IndexPlan`: every chunk the
    quarantine lets through, with the text that is embedded (`embedded_text`:
    the chunk's context header, a blank line and the chunk), the hash that
    finds that text's embedding in the cache (`text_hash`), the hash that
    groups identical copies of its section (`section_hash`) and the stems of
    the BM25 index. `document_scopes` gives each file the framework areas,
    procurements, agreements, agreement pages and document type a search can
    be limited to, and `agreement_procurements` the procurement of each
    agreement in the register.

Why:
    The search must find only what step 5 lets through (fail closed), and the
    offline measurement (`evals/`) must index exactly what production
    indexes. Pure functions on plain values give both: the `index` command
    and the measurement call the same ones, with no database or network.

How:
    The quarantine is applied per chunk with `Quarantine.holds`, and the
    chunks it holds are counted. The context header names the agreement, the
    document and the heading path, which a chunk such as "Vitet uppgår till
    5 000 kr" does not (contextual retrieval, ADR 0011); the same text is
    embedded and analysed for BM25 (`retrieval/swedish_text.terms`). The
    section hash is taken over the section's text without its leading section
    number and with all whitespace collapsed to single spaces, so copies that
    differ only in line breaks, or in the number a document gives the clause,
    group together: in the pilot "Prismodeller" is 6.17 of one set of general
    terms, 2.16 of another and 7.16 of a procurement document, word for word
    (464 of the 1,333 groups with copies). The heading's title stays in the
    hash, so two clauses that only say "Se bilaga Priser" under different
    headings stay apart.

    A file's scope comes from every agreement page that links to it, as its
    context header does (`pipeline.link_infos`): pages no longer listed on
    avropa.se count too, since step 5 holds back a file when all its pages
    are gone. Procurement and agreement numbers are stored in the register's
    spelling when the register has them (compared by `procurement_key` and
    `agreement_key`, as in step 5), so a search filter, which the register
    spells, matches a page that writes "23.3.2649-22" for "23.3-2649-2022".
    A number the register lacks is kept as written. The framework areas are
    the register's for the file's procurements, and the document type is
    step 4's.
"""

import hashlib
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from avtalsagent.domain.documents import CatalogLink
from avtalsagent.domain.extracted import Quarantine
from avtalsagent.domain.identifiers import agreement_key, procurement_key
from avtalsagent.domain.register import RegisterEntry
from avtalsagent.domain.search import ChunkKey, DocumentScope, IndexedChunk, IndexPlan
from avtalsagent.retrieval import swedish_text


@dataclass(frozen=True)
class IndexSource:
    """A stored chunk as step 6 reads it, with the whole text of its section."""

    key: ChunkKey
    context_header: str  # "ramavtal › dokument › rubrikstig" (step 3)
    text: str
    section_text: str  # starts with the heading line, number included
    section_number: str | None  # e.g. "6.21.9"; None for a section without one


def embedded_text(context_header: str, text: str) -> str:
    """The text that is embedded and analysed: the context header, a blank line, the chunk."""
    return f"{context_header}\n\n{text}"


def text_hash(text: str) -> str:
    """The sha256 of the text's UTF-8 bytes, in hex: the key of the embedding cache."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def section_hash(section_text: str, section_number: str | None) -> str:
    """The hash of a section's text without its number, whitespace collapsed; copies share it."""
    text = section_text
    # The number must end at a space, or a dot and a space: "6.1" is not the start of "6.17".
    if section_number and (leading := re.match(re.escape(section_number) + r"\.?(?=\s|$)", text)):
        text = text[leading.end() :]
    return text_hash(" ".join(text.split()))


def plan_index(sources: Iterable[IndexSource], quarantine: Quarantine) -> IndexPlan:
    """Every chunk the quarantine lets through, in key order, and how many it holds back."""
    chunks: list[IndexedChunk] = []
    held_back = 0
    for source in sorted(sources, key=lambda source: source.key):
        if quarantine.holds(source.key.sha256, source.key.section_position):
            held_back += 1
            continue
        text = embedded_text(source.context_header, source.text)
        chunks.append(
            IndexedChunk(
                key=source.key,
                embedded_text=text,
                text_hash=text_hash(text),
                section_hash=section_hash(source.section_text, source.section_number),
                terms=tuple(swedish_text.terms(text)),
            )
        )
    return IndexPlan(chunks=tuple(chunks), held_back=held_back)


def document_scopes(
    links: Sequence[CatalogLink],
    register: Sequence[RegisterEntry],
    agreement_numbers: Mapping[str, str | None],
    document_types: Mapping[str, str],
) -> dict[str, DocumentScope]:
    """The scope of every linked file, by its hash.

    `agreement_numbers` and `document_types` are each file's agreement number
    and document type from step 4 (its `document_metadata` row); the number is
    set for a file in a supplier's card.
    """
    areas: defaultdict[str, set[str]] = defaultdict(set)
    procurement_spelling: dict[str, str] = {}
    agreement_spelling: dict[str, str] = {}
    for entry in register:
        if key := procurement_key(entry.procurement_number):
            areas[key].add(entry.framework_area)
            procurement_spelling[key] = entry.procurement_number
        if key := agreement_key(entry.agreement_number):
            agreement_spelling[key] = entry.agreement_number

    def register_spelling(number: str, key: str | None, spelling: Mapping[str, str]) -> str:
        return spelling.get(key, number) if key else number

    links_by_sha: defaultdict[str, list[CatalogLink]] = defaultdict(list)
    for link in links:
        links_by_sha[link.sha256].append(link)

    scopes: dict[str, DocumentScope] = {}
    for sha256, file_links in sorted(links_by_sha.items()):
        written = {number for link in file_links for number in link.page_procurement_numbers}
        keys = {key for number in written if (key := procurement_key(number))}
        procurements = {
            register_spelling(number, procurement_key(number), procurement_spelling)
            for number in written
        }
        agreements = {link.agreement_number for link in file_links if link.agreement_number}
        if own := agreement_numbers.get(sha256):
            agreements.add(own)
        scopes[sha256] = DocumentScope(
            sha256=sha256,
            framework_areas=tuple(sorted({area for key in keys for area in areas[key]})),
            procurement_numbers=tuple(sorted(procurements)),
            agreement_numbers=tuple(
                sorted(
                    {
                        register_spelling(number, agreement_key(number), agreement_spelling)
                        for number in agreements
                    }
                )
            ),
            page_titles=tuple(sorted({link.page_title for link in file_links})),
            document_type=document_types.get(sha256),
        )
    return scopes


def agreement_procurements(register: Sequence[RegisterEntry]) -> dict[str, str]:
    """Each agreement number of the register with its procurement number."""
    return {entry.agreement_number: entry.procurement_number for entry in register}
