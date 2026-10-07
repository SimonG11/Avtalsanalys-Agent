"""The gold questions, and the sections a search must find for each of them.

What:
    `load_gold` reads the gold file (evals/datasets/gold_sv.jsonl, one JSON
    object per line) into frozen `GoldQuestion`s, and `parse_gold` does the
    same for its text. `skip_reason` says why a question is not measured by
    the search's evaluation, `search_filters` gives the filters of a
    question's scope, and `resolve` turns each document source of a question
    into a required source: the hashes of the sections its alternatives are
    in. `retrieval_questions` does all three for a whole file. `resolve`
    reads the stored sections through a `SectionLookup`; `DatabaseSections`
    is the one on `document_section`. `GoldError` is raised for a line that
    does not have the expected form or a source that does not match the
    stored sections.

Why:
    The gold file names a source as a person found it: the file, the section
    number (or, for a section without one, its position) and a quote. The
    search ranks section groups by `section_hash`, so the sources must be
    turned into those hashes with the function the index uses
    (`step6_index.section_hash`), or a found section would not count. The
    quote must be in the section: after a change to step 3 a number can be
    read differently or a position can move, and the measurement would
    otherwise score against the wrong section without a word. Parsing needs
    no database, so it can be tested alone and the agent evaluation can read
    the same file.

How:
    Each line is checked field by field. Unknown keys are refused in the
    scope, a document source and an alternative, where a misspelt key
    ("section_postion") would silently change what is measured; elsewhere
    they are ignored. An alternative is found by (sha256, section_position)
    when the position is given, else by (sha256, section_number), and
    exactly one candidate section must contain the quote verbatim. When
    both a position and a number are given, the section at the position
    must have that number, so a moved position is caught even where the
    quote is generic. The hash is taken over the stored section's text and
    number, as step 6 does. Two required sources of one question must not
    share a hash, since the metrics count each section for one source only.
    A question is a retrieval question when it has at least one document
    source; register-only questions and questions the agreements do not
    answer belong to the agent evaluation and are skipped.

    A question that fits several agreements or sub-areas may say whether
    the agent should ask the user which one (`should_ask`), the choices a
    good question offers (`options`) and the user's reply (`clarification`),
    which the answer evaluation gives when the agent asks. The three keys
    are optional, so the gold file reads as before; `should_ask` true needs
    at least two options and a clarification, and options or a
    clarification without `should_ask` are refused, since they would be
    silently unused.
"""

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from avtalsagent.db import models
from avtalsagent.domain.search import SearchFilters
from avtalsagent.ingestion.step6_index import section_hash

_SHA256 = re.compile(r"[0-9a-f]{64}")
_SCOPE_KEYS = frozenset({"framework_area", "agreement_number"})
_DOCUMENT_SOURCE_KEYS = frozenset({"kind", "alternatives"})
_ALTERNATIVE_KEYS = frozenset(
    {
        "sha256",
        "file_title",
        "page_title",
        "section_number",
        "section_position",
        "section_title",
        "pages",
        "quote",
        "note",  # a remark for the person who reads the file
    }
)


class GoldError(ValueError):
    """The gold file cannot be read, or one of its sources is not in the stored sections."""


class SkipReason(StrEnum):
    """Why a question is not measured by the search's evaluation."""

    REGISTER_ONLY = "register_only"  # the answer is in the register, not in a document
    NO_ANSWER = "no_answer"  # the agreements do not answer the question


@dataclass(frozen=True)
class GoldScope:
    """What the question is limited to, as the person asking would say it."""

    framework_area: str | None = None
    agreement_number: str | None = None


@dataclass(frozen=True)
class Alternative:
    """One place the text of a source is found: a section of a file, and a quote from it."""

    sha256: str
    section_number: str | None  # None for a section without a number
    section_position: int | None  # given when the number does not find the section
    section_title: str
    file_title: str
    page_title: str
    pages: tuple[int, ...] | None
    quote: str


@dataclass(frozen=True)
class DocumentSource:
    """A text the answer needs; any of its alternatives (copies of it) will do."""

    alternatives: tuple[Alternative, ...]


@dataclass(frozen=True)
class RegisterSource:
    """Fields of the register the answer needs; read by the agent evaluation."""

    agreement_numbers: tuple[str, ...]
    fields: tuple[str, ...]


@dataclass(frozen=True)
class GoldQuestion:
    id: str
    category: str
    area: str
    scope: GoldScope
    question: str
    answer: str
    answerable: bool
    sources: tuple[DocumentSource | RegisterSource, ...]
    why_hard: str
    difficulty: int
    # Whether the agent should ask the user (ask_user) before it answers; None when the gold
    # does not say. The options a good question offers, and the user's reply to it.
    should_ask: bool | None = None
    options: tuple[str, ...] = ()
    clarification: str | None = None

    @property
    def document_sources(self) -> tuple[DocumentSource, ...]:
        return tuple(source for source in self.sources if isinstance(source, DocumentSource))


@dataclass(frozen=True)
class GoldFile:
    """The questions of a gold file, and which file it was."""

    path: Path
    sha256: str  # of the file's bytes, so a report names the gold it was measured on
    questions: tuple[GoldQuestion, ...]


@dataclass(frozen=True)
class StoredSection:
    """A section as step 3 stored it."""

    position: int
    number: str | None
    text: str  # starts with the heading line, number included


class SectionLookup(Protocol):
    """Reads the stored sections of a file."""

    def sections(self, sha256: str) -> Sequence[StoredSection]:
        """Every stored section of the file, in position order; empty for an unknown file."""
        ...


@dataclass(frozen=True)
class RetrievalQuestion:
    """A question the search is measured on, with the sections it must find."""

    question: GoldQuestion
    required: tuple[frozenset[str], ...]  # per document source, its acceptable section hashes
    filters: SearchFilters


@dataclass(frozen=True)
class SkippedQuestion:
    id: str
    category: str
    reason: SkipReason


class DatabaseSections:
    """A `SectionLookup` on the stored sections (`document_section`), read once per file."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._files: dict[str, list[StoredSection]] = {}

    def sections(self, sha256: str) -> list[StoredSection]:
        if sha256 not in self._files:
            section = models.DocumentSection
            rows = self._session.execute(
                select(section.position, section.number, section.text)
                .where(section.sha256 == sha256)
                .order_by(section.position)
            )
            self._files[sha256] = [
                StoredSection(position, number, text) for position, number, text in rows
            ]
        return self._files[sha256]


def load_gold(path: Path) -> GoldFile:
    """The questions of the gold file at `path`; `GoldError` if it cannot be read or parsed."""
    try:
        content = path.read_bytes()
    except OSError as error:
        raise GoldError(f"cannot read the gold file {path}: {error.strerror}") from None
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        raise GoldError(f"the gold file {path} is not UTF-8") from None
    return GoldFile(path, hashlib.sha256(content).hexdigest(), parse_gold(text))


def parse_gold(text: str) -> tuple[GoldQuestion, ...]:
    """The questions of a gold file's text, one JSON object per line; blank lines are skipped."""
    questions: list[GoldQuestion] = []
    seen: set[str] = set()
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            data: object = json.loads(line)
        except json.JSONDecodeError as error:
            raise GoldError(f"line {number}: not valid JSON ({error.msg})") from None
        question = _question(_object(data, f"line {number}"), f"line {number}")
        if question.id in seen:
            raise GoldError(f"line {number}: the id {question.id} is used twice")
        seen.add(question.id)
        questions.append(question)
    if not questions:
        raise GoldError("the gold file has no questions")
    return tuple(questions)


def skip_reason(question: GoldQuestion) -> SkipReason | None:
    """None for a retrieval question (one with a document source), else why it is skipped."""
    if question.document_sources:
        return None
    return SkipReason.REGISTER_ONLY if question.answerable else SkipReason.NO_ANSWER


def search_filters(question: GoldQuestion) -> SearchFilters:
    """The filters of the question's scope."""
    return SearchFilters(
        framework_area=question.scope.framework_area,
        agreement_number=question.scope.agreement_number,
    )


def resolve(question: GoldQuestion, lookup: SectionLookup) -> tuple[frozenset[str], ...]:
    """Each document source of the question as the section hashes of its alternatives."""
    required: list[frozenset[str]] = []
    for number, source in enumerate(question.document_sources, start=1):
        by_hash = {
            _resolve_alternative(question.id, alternative, lookup): alternative
            for alternative in source.alternatives
        }
        for earlier, hashes in enumerate(required, start=1):
            if shared := hashes.intersection(by_hash):
                alternative = by_hash[min(shared)]
                raise GoldError(
                    f"{question.id}: document sources {earlier} and {number} are the same "
                    f"section ({_where(alternative)}); merge them into one source"
                )
        required.append(frozenset(by_hash))
    return tuple(required)


def retrieval_questions(
    questions: Iterable[GoldQuestion], lookup: SectionLookup
) -> tuple[list[RetrievalQuestion], list[SkippedQuestion]]:
    """The retrieval questions with their required sources, and the questions skipped."""
    measured: list[RetrievalQuestion] = []
    skipped: list[SkippedQuestion] = []
    for question in questions:
        reason = skip_reason(question)
        if reason is not None:
            skipped.append(SkippedQuestion(question.id, question.category, reason))
        else:
            required = resolve(question, lookup)
            measured.append(RetrievalQuestion(question, required, search_filters(question)))
    return measured, skipped


def _resolve_alternative(question_id: str, alternative: Alternative, lookup: SectionLookup) -> str:
    """The hash of the one section the alternative points at that contains its quote."""
    where = f"{question_id}: {_where(alternative)}"
    sections = lookup.sections(alternative.sha256)
    if not sections:
        raise GoldError(f"{where}: the file has no stored sections (run `process`?)")
    position = alternative.section_position
    if position is not None:
        candidates = [section for section in sections if section.position == position]
        if not candidates:
            raise GoldError(f"{where}: the file has no section at that position")
        found = candidates[0].number
        if found != alternative.section_number:
            raise GoldError(
                f"{where}: the section at that position is "
                f"{f'numbered {found}' if found else 'unnumbered'}; the positions have moved"
            )
    else:
        candidates = [s for s in sections if s.number == alternative.section_number]
        if not candidates:
            raise GoldError(f"{where}: the file has no section with that number")
    matching = [section for section in candidates if alternative.quote in section.text]
    if not matching:
        raise GoldError(f"{where}: the quote is not in the section")
    if len(matching) > 1:
        positions = ", ".join(str(section.position) for section in matching)
        raise GoldError(
            f"{where}: the quote is in {len(matching)} sections with that number "
            f"(positions {positions}); give section_position"
        )
    section = matching[0]
    return section_hash(section.text, section.number)


def _where(alternative: Alternative) -> str:
    """'section 9.25.4 of b6275789837f', with the position when it is given."""
    file = alternative.sha256[:12]
    position = alternative.section_position
    if alternative.section_number is None:
        return f"the unnumbered section at position {position} of {file}"
    if position is None:
        return f"section {alternative.section_number} of {file}"
    return f"section {alternative.section_number} (position {position}) of {file}"


# --- Parsing ----------------------------------------------------------------------------------


def _question(data: Mapping[str, object], where: str) -> GoldQuestion:
    question_id = _text(data, "id", where)
    where = f"{where} ({question_id})"
    answerable = _flag(data, "answerable", where)
    sources = tuple(
        _source(_object(item, f"{where}, source {number}"), f"{where}, source {number}")
        for number, item in enumerate(_list(data, "sources", where), start=1)
    )
    if answerable and not sources:
        raise GoldError(f"{where}: an answerable question needs at least one source")
    should_ask, options, clarification = _asking(data, where)
    return GoldQuestion(
        id=question_id,
        category=_text(data, "category", where),
        area=_text(data, "area", where),
        scope=_scope(_object(_field(data, "scope", where), f"{where}, scope"), f"{where}, scope"),
        question=_text(data, "question", where),
        answer=_text(data, "answer", where, blank=True),
        answerable=answerable,
        sources=sources,
        why_hard=_text(data, "why_hard", where, blank=True),
        difficulty=_whole(data, "difficulty", where),
        should_ask=should_ask,
        options=options,
        clarification=clarification,
    )


def _asking(
    data: Mapping[str, object], where: str
) -> tuple[bool | None, tuple[str, ...], str | None]:
    """`should_ask`, `options` and `clarification`, each optional, checked together."""
    should_ask = None if data.get("should_ask") is None else _flag(data, "should_ask", where)
    options = () if data.get("options") is None else _texts(data, "options", where)
    if any(not option.strip() for option in options):
        raise GoldError(f"{where}: an option is blank")
    clarification = _optional_text(data, "clarification", where)
    if should_ask is None and (options or clarification is not None):
        raise GoldError(f"{where}: options and clarification need should_ask")
    if should_ask and len(options) < 2:
        raise GoldError(f"{where}: should_ask needs at least two options")
    if should_ask and clarification is None:
        raise GoldError(f"{where}: should_ask needs a clarification")
    return should_ask, options, clarification


def _scope(data: Mapping[str, object], where: str) -> GoldScope:
    _known_keys(data, _SCOPE_KEYS, where)
    return GoldScope(
        framework_area=_optional_text(data, "framework_area", where),
        agreement_number=_optional_text(data, "agreement_number", where),
    )


def _source(data: Mapping[str, object], where: str) -> DocumentSource | RegisterSource:
    kind = _text(data, "kind", where)
    if kind == "register":
        return RegisterSource(
            agreement_numbers=_texts(data, "agreement_numbers", where),
            fields=_texts(data, "fields", where),
        )
    if kind != "document":
        raise GoldError(f"{where}: unknown kind {kind!r} (document or register)")
    _known_keys(data, _DOCUMENT_SOURCE_KEYS, where)
    items = _list(data, "alternatives", where)
    if not items:
        raise GoldError(f"{where}: a document source needs at least one alternative")
    return DocumentSource(
        tuple(
            _alternative(_object(item, f"{where}, alternative {n}"), f"{where}, alternative {n}")
            for n, item in enumerate(items, start=1)
        )
    )


def _alternative(data: Mapping[str, object], where: str) -> Alternative:
    _known_keys(data, _ALTERNATIVE_KEYS, where)
    sha256 = _text(data, "sha256", where)
    if not _SHA256.fullmatch(sha256):
        raise GoldError(f"{where}: sha256 must be 64 lowercase hex digits")
    number = _optional_text(data, "section_number", where, required=True)
    position: int | None = None
    if data.get("section_position") is not None:
        position = _whole(data, "section_position", where)
    if number is None and position is None:
        raise GoldError(f"{where}: a section without a number needs its section_position")
    pages: tuple[int, ...] | None = None
    if (listed := data.get("pages")) is not None:
        if not isinstance(listed, list) or not all(_is_int(page) for page in listed):
            raise GoldError(f"{where}: pages must be a list of page numbers or null")
        pages = tuple(listed)
    note = data.get("note")
    if note is not None and not isinstance(note, str):
        raise GoldError(f"{where}: note must be a string")
    return Alternative(
        sha256=sha256,
        section_number=number,
        section_position=position,
        section_title=_text(data, "section_title", where, blank=True),
        file_title=_text(data, "file_title", where, blank=True),
        page_title=_text(data, "page_title", where, blank=True),
        pages=pages,
        quote=_text(data, "quote", where),
    )


def _object(value: object, where: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise GoldError(f"{where}: expected a JSON object")
    return {str(key): item for key, item in value.items()}


def _field(data: Mapping[str, object], key: str, where: str) -> object:
    if key not in data:
        raise GoldError(f"{where}: {key} is missing")
    return data[key]


def _text(data: Mapping[str, object], key: str, where: str, blank: bool = False) -> str:
    value = _field(data, key, where)
    if not isinstance(value, str):
        raise GoldError(f"{where}: {key} must be a string")
    if not blank and not value.strip():
        raise GoldError(f"{where}: {key} is blank")
    return value


def _optional_text(
    data: Mapping[str, object], key: str, where: str, required: bool = False
) -> str | None:
    """A non-blank string or null; a missing key is null unless it is `required`."""
    if required:
        _field(data, key, where)
    if data.get(key) is None:
        return None
    return _text(data, key, where)


def _texts(data: Mapping[str, object], key: str, where: str) -> tuple[str, ...]:
    items = _list(data, key, where)
    if not all(isinstance(item, str) for item in items):
        raise GoldError(f"{where}: {key} must be a list of strings")
    return tuple(str(item) for item in items)


def _list(data: Mapping[str, object], key: str, where: str) -> list[object]:
    value = _field(data, key, where)
    if not isinstance(value, list):
        raise GoldError(f"{where}: {key} must be a list")
    return value


def _flag(data: Mapping[str, object], key: str, where: str) -> bool:
    value = _field(data, key, where)
    if not isinstance(value, bool):
        raise GoldError(f"{where}: {key} must be true or false")
    return value


def _whole(data: Mapping[str, object], key: str, where: str) -> int:
    value = _field(data, key, where)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise GoldError(f"{where}: {key} must be a whole number of at least 0")
    return value


def _is_int(value: object) -> bool:
    # JSON's true and false are ints in Python.
    return isinstance(value, int) and not isinstance(value, bool)


def _known_keys(data: Mapping[str, object], known: frozenset[str], where: str) -> None:
    unknown = sorted(data.keys() - known)
    if unknown:
        raise GoldError(f"{where}: unknown key {', '.join(unknown)}")
