"""find_amendments: the amendments and the answers in a questions log that change a section.

What:
    `find_amendments` returns the sections that change a section, or a whole
    file: sections of an amendment, and answers and Kammarkollegiet's messages
    in a questions-and-answers log, each with what it changes (the section, or the whole file), the
    reference as written, its status, its date and the text around the
    reference, newest first. Amending sections the tools may not show are
    counted, not listed. `excerpt`, `answer_date` and `newest_first` are its
    pure parts.

Why:
    Agreements change after they are signed, and a procurement's terms change
    while it is open: "Rättelse. Texten som gäller är följande för punkt 3.2:
    …" (7a765d649e25 §9, answered 2024-02-20) replaces a sentence of the
    tender documents. An answer built on the old wording is wrong even when
    every quote is exact. Step 4 marks these references (`replaces`: a
    replacement verb in an amendment, or in a log's answer after "Publikt
    svar" or message), so the tool reads them backwards from the section asked about
    (ADR 0017) instead of keeping a table of its own. The agent reads each
    amending section with `read_section` and cites it with the section it
    changes; the latest-wording rule of the answer check calls the tool the
    same way.

How:
    The target is found as `read_section` finds a section (`find_section`),
    or as a file (`require_file`), with the same errors. One query reads the
    references with `replaces` and status resolved or ambiguous, in a section
    of an amendment or a log, that point into the file: with a section, to
    that section or to the whole file. The first reference per (amending
    section, amended section) is kept, a resolved one before an ambiguous
    one and then by its place in the text, so a reference stored once per
    agreement page is listed once. An amending section the tools may not
    show, or one that changes a held-back section of the file, is counted
    once in `held_back`, as `references.py` counts the targets it leaves out.
    A log's answer or message is dated by its TendSign stamps (`answer_date`),
    an amendment by its file's version date, else its publication date, else
    the date avropa.se last updated the file.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from avtalsagent.db import models
from avtalsagent.domain.extracted import DocumentType, ReferenceStatus
from avtalsagent.mcp_server.arguments import SectionNumber, SectionPosition, Sha256
from avtalsagent.mcp_server.references import TargetRef, file_targets
from avtalsagent.mcp_server.results import SectionRef, section_refs
from avtalsagent.mcp_server.tools.read_section import find_section, require_file
from avtalsagent.mcp_server.visibility import load_visibility

EXCERPT_CHARS = 400
_BEFORE = 150  # characters of the amending text kept before the reference
_AFTER = 250  # and after it
_CUT = "…"
_SPACE = re.compile(r"\s")
# A TendSign time stamp, "2024-02-20 08:39". A log printed from TendSign also has the time
# of the printout ("Utskrivet: 2022-10-13 09:20", 20c753d88340 §14), which dates no answer.
_STAMP = re.compile(r"(?<!Utskrivet:\s)(\d{4}-\d{2}-\d{2}) \d{2}:\d{2}")

_AMENDING_TYPES = (DocumentType.AMENDMENT.value, DocumentType.QUESTIONS_AND_ANSWERS.value)
_LISTED_STATUSES = (ReferenceStatus.RESOLVED.value, ReferenceStatus.AMBIGUOUS.value)


class Amendment(BaseModel):
    """A section that changes the section or file asked about."""

    amending: SectionRef  # where the change is written: an amendment or a log's answer
    amended: TargetRef  # what it changes; a whole file has the section fields None
    raw: str  # the reference as written in the amending section, e.g. "punkt 3.2"
    status: str  # "resolved", or "ambiguous" (the reference has several candidates)
    dated: date | None  # the answer's TendSign stamp, or the amendment file's date
    excerpt: str  # at most EXCERPT_CHARS of the amending text around the reference


class AmendmentResult(BaseModel):
    """The section or file asked about, and the sections that change it."""

    target: TargetRef  # the section asked about, or the whole file (section fields None)
    amendments: list[Amendment]  # newest first, undated last
    held_back: int  # amending sections left out: held back, or changing a held-back section


def find_amendments(
    session: Session,
    sha256: Sha256,
    section_number: SectionNumber = None,
    section_position: SectionPosition = None,
) -> AmendmentResult:
    """Hitta ändringar av ett avsnitt eller ett dokument.

    En ändring är ett avsnitt i ett ändringsdokument, eller ett svar eller meddelande i
    Frågor och svar, som ersätter, stryker eller lägger till text. Ange dokumentets sha256 och
    avsnittet som till read_section; du får då ändringarna av avsnittet och av hela
    dokumentet. Utan avsnitt får du ändringarna av dokumentet och alla dess avsnitt. amending
    är avsnittet där ändringen står och amended det som ändras; ett amended utan
    section_position är hela dokumentet. excerpt är texten runt hänvisningen raw. Status
    ambiguous betyder att hänvisningen har flera möjliga mål, så ändringen kan gälla ett annat
    dokument: läs den och avgör. dated är svarets eller ändringsdokumentets datum, och de
    nyaste kommer först. Läs ändringen med read_section, bygg svaret på den senaste lydelsen
    och citera både avsnittet och ändringen. held_back räknar ändringar som saknas för att de,
    eller avsnittet de ändrar, hålls tillbaka i granskningen.
    """
    visibility = load_visibility(session)
    position: int | None = None
    if section_number is None and section_position is None:
        require_file(session, visibility, sha256)
    else:
        found = find_section(session, visibility, sha256, section_number, section_position)
        position = found.position

    changes = _changes_to(session, sha256, position)
    kept: dict[tuple[str, int, int | None], _Change] = {}
    held: set[tuple[str, int]] = set()
    for change in changes:
        if not visibility.shows_section(*change.amending) or (
            change.changed is not None and not visibility.shows_section(sha256, change.changed)
        ):
            held.add(change.amending)  # once, also when found through two pages or references
        else:
            kept.setdefault((*change.amending, change.changed), change)  # the first, as ordered

    sections = section_refs(
        session,
        {change.amending for change in kept.values()}
        | {(sha256, change.changed) for change in kept.values() if change.changed is not None}
        | ({(sha256, position)} if position is not None else set()),
    )
    # A shown file has metadata and a scope, so file_targets finds it; a shown section has
    # its rows, so section_refs finds it.
    whole_file = file_targets(session, [sha256])[sha256]
    amendments = [
        Amendment(
            amending=sections[change.amending],
            amended=(
                whole_file
                if change.changed is None
                else TargetRef(**sections[(sha256, change.changed)].model_dump())
            ),
            raw=change.raw,
            status=change.status,
            dated=change.dated,
            excerpt=excerpt(change.text, change.start, change.end),
        )
        for change in kept.values()
    ]
    return AmendmentResult(
        target=(
            whole_file
            if position is None
            else TargetRef(**sections[(sha256, position)].model_dump())
        ),
        amendments=newest_first(amendments),
        held_back=len(held),
    )


@dataclass(frozen=True)
class _Change:
    """A reference that changes the file, with what an `Amendment` needs of its section."""

    amending: tuple[str, int]  # the section the reference is in: sha256, position
    changed: int | None  # the section of the file it changes; None for the whole file
    raw: str
    status: str
    dated: date | None
    text: str  # the amending section's
    start: int  # the reference's offsets in `text`
    end: int


def _changes_to(session: Session, sha256: str, position: int | None) -> list[_Change]:
    """The references that change the file, or the section at `position` or the whole file.

    Only references with `replaces`, resolved or ambiguous, in an amendment
    or a questions log. Ordered by amending section; in each, the resolved
    references first, then by their place in the text.
    """
    reference, target = models.DocumentReference, models.ReferenceTarget
    metadata, section = models.DocumentMetadata, models.DocumentSection
    query = (
        select(
            reference.sha256,
            reference.section_position,
            reference.char_start,
            reference.char_end,
            reference.raw,
            reference.status,
            target.target_section_position,
            metadata.document_type,
            metadata.version_date,
            metadata.published_on,
            metadata.site_updated,
            section.text,
        )
        .join(target, target.reference_id == reference.id)
        .join(metadata, metadata.sha256 == reference.sha256)
        .join(
            section,
            (section.sha256 == reference.sha256) & (section.position == reference.section_position),
        )
        .where(
            reference.replaces.is_(True),
            reference.status.in_(_LISTED_STATUSES),
            metadata.document_type.in_(_AMENDING_TYPES),
            target.target_sha256 == sha256,
        )
        .order_by(
            reference.sha256,
            reference.section_position,
            reference.status != ReferenceStatus.RESOLVED.value,  # resolved (false) first
            reference.char_start,
            reference.id,
            target.position,
        )
    )
    if position is not None:
        query = query.where(
            or_(
                target.target_section_position == position,
                target.target_section_position.is_(None),
            )
        )
    return [
        _Change(
            amending=(row.sha256, row.section_position),
            changed=row.target_section_position,
            raw=row.raw,
            status=row.status,
            dated=(
                answer_date(row.text)
                if row.document_type == DocumentType.QUESTIONS_AND_ANSWERS.value
                else row.version_date or row.published_on or row.site_updated
            ),
            text=row.text,
            start=row.char_start,
            end=row.char_end,
        )
        for row in session.execute(query)
    ]


def excerpt(text: str, start: int, end: int) -> str:
    """At most `EXCERPT_CHARS` of `text` around `text[start:end]`, on one line.

    From up to `_BEFORE` characters before the reference to up to `_AFTER`
    after it, cut between words, with the whitespace collapsed and "…" where
    text is left out.
    """
    begin, finish = max(0, start - _BEFORE), min(len(text), end + _AFTER)
    # Not in the middle of a word, and never into the reference.
    if begin > 0 and not text[begin - 1].isspace():
        space = _SPACE.search(text, begin, start)
        begin = space.start() if space else begin
    if finish < len(text) and not text[finish].isspace():
        finish = max(
            (space.start() for space in _SPACE.finditer(text, end, finish)), default=finish
        )
    head = _CUT if text[:begin].strip() else ""
    tail = _CUT if text[finish:].strip() else ""
    words = " ".join(text[begin:finish].split())
    room = EXCERPT_CHARS - len(head) - len(_CUT)
    if len(head) + len(words) + len(tail) > EXCERPT_CHARS:
        cut = words[: room + 1]  # a space after the last whole word is kept, then dropped
        words, tail = (cut.rsplit(" ", 1)[0] if " " in cut else cut[:room]), _CUT
    return head + words + tail


def answer_date(text: str) -> date | None:
    """When the answer in a section of a questions log was given: its latest TendSign stamp.

    An answer comes after its question, but the parser's reading order mixes
    their stamps: 20c753d88340 §2 has both after "Publikt svar", the
    question's (2022-03-02 09:12) first, and 39d8c1efe373 §15 both before
    it. Other stamps in an answer name earlier messages ("Se
    informationsmeddelande publicerat 2024-06-03 09:16", bdf58b81d100 §114).
    None for a section without a stamp.
    """
    days = []
    for match in _STAMP.finditer(text):
        try:
            days.append(date.fromisoformat(match[1]))
        except ValueError:
            continue
    return max(days, default=None)


def newest_first(amendments: Iterable[Amendment]) -> list[Amendment]:
    """The amendments newest first and undated last; then by file title and place.

    The title, hash and positions only make the order stable: the same call
    gives the same list.
    """
    return sorted(
        amendments,
        key=lambda found: (
            found.dated is None,
            -found.dated.toordinal() if found.dated else 0,
            found.amending.file_title,
            found.amending.sha256,
            found.amending.section_position,
            found.amended.section_position is None,  # a section before the whole file
            found.amended.section_position or 0,
        ),
    )
