"""Ingestion step 4: what each file is, what it states and what it refers to.

What:
    `extract_document` gives one file's `DocumentExtraction`: its metadata (type,
    annex number, first chapter, TendSign cover, version and publication dates,
    whether it is a template), its facts (case, agreement and organisation
    numbers, the parties, the agreement period, signature dates) and the
    reference mentions in its sections. `extract_corpus` does this for every
    file, resolves the mentions against the other files of the same agreement
    pages (`extract/reference_resolver.py`) and, when a matcher is given, lets a
    language model choose a heading where the rules found only the file
    (`extract/title_matcher.py`).

Why:
    Step 5 compares the facts with the register, and the agent (M6) follows the
    references. Each kind of fact has its own module under `ingestion/extract/`,
    with its rules and the counts measured on the pilot. This module only puts
    them together and decides what needs more than one of them: whether a file
    is a template, its version date (which can come from its signatures) and
    the spelling of its numbers.

How:
    1. The type comes first, from the links (`document_type.classify`), since
       the mention finder needs it.
    2. Facts are read from every block of the parsed file, page headers and
       footers included: 34 files state their case number only there (M4
       design, decision 1). Mentions are read from the sections of step 3,
       since a reference is an edge between sections.
    3. A case or agreement number is found as its key ("23.3-2940-2020-018")
       and stored in the register's spelling when the register has that key
       ("23.3-2940-20:018"), so the report and the database write it as the
       register does (decision 2). The checks compare keys either way.
    4. `is_template` (decision 4): the type TEMPLATE, or a date field
       ("[DATUM]") in a file that states no start or end of an agreement
       period. An empty supplier or number field alone never makes a template:
       the main document of every area leaves the supplier slot empty.
    5. The title is the link text used most often, as in step 3's context
       header; `site_updated` is the latest "Senast uppdaterad" of the links.
"""

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from avtalsagent.domain.documents import CatalogLink
from avtalsagent.domain.extracted import (
    DocumentExtraction,
    DocumentMetadata,
    DocumentType,
    Fact,
    FactKind,
    Reference,
)
from avtalsagent.domain.identifiers import agreement_key, procurement_key
from avtalsagent.domain.parsed import ParsedDocument
from avtalsagent.domain.register import RegisterEntry
from avtalsagent.ingestion.extract.dates import find_dates
from avtalsagent.ingestion.extract.document_type import (
    annex_number,
    classify,
    first_chapter,
    published_on,
    tendsign_cover,
    version_date,
)
from avtalsagent.ingestion.extract.identifiers import find_identifiers
from avtalsagent.ingestion.extract.parties import find_parties
from avtalsagent.ingestion.extract.reference_patterns import find_mentions
from avtalsagent.ingestion.extract.reference_resolver import (
    CorpusDocument,
    resolution_rate,
    resolve,
)
from avtalsagent.ingestion.extract.title_matcher import MatchStats, TitleMatcher, match_titles
from avtalsagent.ingestion.step3_chunk import ChunkedDocument, OutlineKind

_NUMBER_KINDS = (FactKind.PROCUREMENT_NUMBER, FactKind.AGREEMENT_NUMBER)
_PERIOD_LIMITS = (FactKind.PERIOD_START, FactKind.PERIOD_END)
_DATE_FIELD = "date"  # the value of a PLACEHOLDER fact for an unfilled date (dates.py, P10)


@dataclass(frozen=True)
class ExtractionInput:
    """One file as step 4 reads it: parsed (step 2), split (step 3), and its links."""

    parsed: ParsedDocument
    chunked: ChunkedDocument
    links: tuple[CatalogLink, ...]  # at least one: a file no page links to is not processed


@dataclass(frozen=True)
class RegisterSpelling:
    """The register's spelling of each case and agreement number, by key."""

    numbers: Mapping[str, str]

    @classmethod
    def of(cls, entries: Iterable[RegisterEntry]) -> "RegisterSpelling":
        """The spellings of a register's procurement and agreement numbers.

        A procurement number is entered before the agreement numbers, so an
        agreement number without a sequence ("23.5-3718-2024"), which has its
        procurement's key, is written as the procurement is.
        """
        entries = list(entries)
        numbers: dict[str, str] = {}
        for entry in entries:
            if key := procurement_key(entry.procurement_number):
                numbers.setdefault(key, entry.procurement_number)
        for entry in entries:
            if key := agreement_key(entry.agreement_number):
                numbers.setdefault(key, entry.agreement_number)
        return cls(numbers)

    def respell(self, fact: Fact) -> Fact:
        """The fact with its number in the register's spelling, when the register has it."""
        if fact.kind not in _NUMBER_KINDS or fact.value not in self.numbers:
            return fact
        return fact.model_copy(update={"value": self.numbers[fact.value]})

    def agreement_number(self, raw: str) -> str:
        """An agreement number from a link, in the register's spelling when it has it."""
        key = agreement_key(raw)
        return self.numbers.get(key, raw) if key is not None else raw


@dataclass(frozen=True)
class CorpusExtraction:
    """What step 4 found in all files of a run."""

    extractions: list[DocumentExtraction]
    # One per mention; after the language model when a matcher was given.
    references: list[Reference]
    # The share of resolved references by the rules alone (the language model left out),
    # so a run without a key can be compared with one with it.
    rules_rate: float
    match_stats: MatchStats | None  # None when no matcher was given


def extract_document(
    parsed: ParsedDocument,
    chunked: ChunkedDocument,
    links: Sequence[CatalogLink],
    spelling: RegisterSpelling,
) -> DocumentExtraction:
    """The metadata, facts and reference mentions of one file."""
    document_type, type_rule = classify(links)
    blocks = parsed.blocks
    found = [*find_identifiers(blocks), *find_parties(blocks), *find_dates(blocks)]
    # Sorted by block, keeping each module's order within a block.
    facts = tuple(spelling.respell(fact) for fact in sorted(found, key=lambda fact: fact.block))
    signed_on = [
        date.fromisoformat(fact.value) for fact in facts if fact.kind is FactKind.SIGNED_ON
    ]
    version, version_rule = version_date(parsed, links, signed_on)

    date_field = any(
        fact.kind is FactKind.PLACEHOLDER and fact.value == _DATE_FIELD for fact in facts
    )
    states_period = any(fact.kind in _PERIOD_LIMITS for fact in facts)
    card = next((link.agreement_number for link in links if link.agreement_number), None)

    metadata = DocumentMetadata(
        sha256=parsed.sha256,
        title=_most_common_title(links),
        document_type=document_type,
        type_rule=type_rule,
        agreement_number=spelling.agreement_number(card) if card else None,
        annex_number=annex_number(links),
        first_chapter=first_chapter(chunked.sections, chunked.outline),
        tendsign_cover=tendsign_cover(parsed),
        is_template=document_type is DocumentType.TEMPLATE or (date_field and not states_period),
        version_date=version,
        version_rule=version_rule,
        published_on=published_on(parsed),
        site_updated=max((link.site_updated for link in links if link.site_updated), default=None),
    )
    mentions = find_mentions(
        chunked.sections, document_type, chunked.outline is OutlineKind.QUESTIONS
    )
    return DocumentExtraction(metadata=metadata, facts=facts, mentions=tuple(mentions))


def extract_corpus(
    inputs: Sequence[ExtractionInput],
    spelling: RegisterSpelling,
    matcher: TitleMatcher | None = None,
) -> CorpusExtraction:
    """Extract every file, resolve the references, then ask the matcher if one is given."""
    extractions = [
        extract_document(item.parsed, item.chunked, item.links, spelling) for item in inputs
    ]
    documents = [
        CorpusDocument(
            metadata=extraction.metadata,
            sections=tuple(item.chunked.sections),
            mentions=extraction.mentions,
            links=item.links,
        )
        for item, extraction in zip(inputs, extractions, strict=True)
    ]
    references = resolve(documents)
    rules_rate = resolution_rate(references)
    stats = None
    if matcher is not None:
        sections_by_sha = {item.parsed.sha256: item.chunked.sections for item in inputs}
        references, stats = match_titles(references, sections_by_sha, matcher)
    return CorpusExtraction(extractions, references, rules_rate, stats)


def _most_common_title(links: Sequence[CatalogLink]) -> str:
    """The link text used most often; on a tie the shorter one, then the first alphabetically."""
    counts = Counter(link.title for link in links)
    return min(counts, key=lambda title: (-counts[title], len(title), title))
