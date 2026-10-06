"""Ingestion step 4: the agreement period, its extension, planned starts and signature dates.

What:
    `find_dates` reads every block of a parsed file and gives one `Fact` per
    date or length of the agreement period it states: PERIOD_START and
    PERIOD_END (ISO dates), PERIOD_MONTHS (the length), EXTENSION_MONTHS (the
    longest extension, "0" for "Ingen förlängning"), PLANNED_START (when a
    procurement document expects the agreement to start), SIGNED_ON (the date
    of an electronic signature) and PLACEHOLDER with value "date" (an unfilled
    date field of a template). `parse_period` reads the period an agreement
    page states ("2024-11-14 - 2028-11-13").

Why:
    Step 5 holds a document back when the period it states deviates from the
    register (architecture plan, section 4). The period is written in a few
    fixed wordings, and a length in words always agrees with its digits (in
    the pilot: 2,931 lengths, 0 disagreements), so rules are enough; no date
    needed a language model. The blocks are read, not the sections (ADR 0009
    decision 2): the Microsoft volume agreement states its period only in a
    page footer, which step 3 removes (171a3cacf5fd footer: "Volymavtalets
    huvuddokument 1.0 för avtalsperiod 2024-05-01 - 2027-04-30"). A guide can
    state one period per sub-area, so a fact says which sub-area it is for
    (`Fact.scope`) and which start goes with which end (`Fact.statement`):
    4b6c2a533fae b39-43 "Ramavtalet för område 3 - IT-säkerhet är giltigt från
    och med 2026-03-10 och till och med 2030-03-09", one list item per area.

How:
    The rules, each with its id in `Fact.rule` (D is an ISO date), and what
    they found in the 207 pilot files (matches / files):
    - P1 cover (10/10): the cover table of a TendSign printout, "Startdatum D
      ... Slutdatum D", and "Förlängning Ingen förlängning" after it.
    - P2 in force (13/12): "träder i kraft (den|per) D", said of the
      agreement: "Ramavtalet", "Volymavtalet", "avtalet" or an area code
      ("AO5") earlier in the sentence, or "träder Ramavtalet i kraft".
    - P3 end (11/11): "längst till och med (den) D".
    - P4 earliest start (18/18): "tidigast från och med (den) D".
    - P5 range in words (6/2): "giltigt|gäller från och med D till och med D",
      "med start från D och slutar D".
    - P6 bare range (11/3): "D - D" after "avtalsperiod" or "avtalstid" in
      the same sentence, or in a table whose header row has one.
    - P7 length (66/54): "löper (därefter) under en period av N månader|år".
    - P8 extension (23/21): "förlängning ... uppgå till|om|med (högst|maximalt)
      N månader|år", within one sentence; the longest length from there to
      the end of the sentence ("med 12 månader i taget, dock maximalt 24
      månader": 24).
    - P9 planned start (14/14): "beräknas träda i kraft (tidigast) D".
    - P10 date field (55/25): "[DATUM ...]", "ÅÅ-MM-DD", "20xx-xx-xx",
      "insert date".
    - P11 signature (52/25): "D HH:MM" on the pages of an e-signature
      certificate (`step3_chunk.signature_certificate_page`).
    A date that does not exist ("2023-02-30") gives no fact. A length is a
    whole number of months; a year counts as twelve. A number can be written
    in digits ("48"), in words ("tre") or both ("trettio (30)", read from the
    digits whatever the word).

    Windows: a rule reads one block at a time; every clause of the pilot is
    within one paragraph. The exception is P1: the layout model sometimes
    reads the cells of the cover table as separate blocks (0692da436391
    b7-b9: "Kontaktperson Startdatum 2025-08-19", "Slutdatum 2029-08-18",
    "Förlängning Ingen förlängning"), so P1 reads each block together with
    the next two, and a match belongs to the block it starts in.

    Statements: the period facts of one block share a statement number; in a
    table, each row is a statement of its own (49f36699a469 b83, one row per
    sub-area: "Programvaror och Tjänster Systemutveckling | 2023-11-01 -
    2027-10-31"), and facts for different sub-areas never share one.
    PLANNED_START, SIGNED_ON and PLACEHOLDER facts have no statement: they
    are never compared with the register.

    Scope: the last sub-area named before the fact in its block or row, as
    written: "område 3 - IT-säkerhet" (P5 above) or the area code "AO5"
    (4b6c2a533fae b16: "Uppdaterad version när nya AO5 träder i kraft per
    2025-08-23"). A bare range in a table row with no such name takes the
    row's first cell ("Programvaror och Tjänster Systemutveckling").
"""

import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import date

from avtalsagent.domain.extracted import Fact, FactKind
from avtalsagent.domain.parsed import CELL_SEPARATOR, Block, BlockKind
from avtalsagent.ingestion.step3_chunk import signature_certificate_page

# An ISO date, not glued to other digits: "2023-06-1501 Sida 3 (13)" (3b22b96ca023
# page header, the page number run into the date) is no date.
_DATE = r"(?<![\d.-])(?:19|20)\d{2}-\d{2}-\d{2}(?!\d)"
# Between a label and its value in a table: "Slutdatum | 2028-11-13", or a line break.
_SEP = r"(?:\s*\|)*\s*"

# Numbers written in words alone: "en period av fyra år". A word followed by its digits
# ("fyrtioåtta (48)", "trettio (30)") is read from the digits, so it need not be here.
_NUMBER_WORDS = {
    "en": 1,
    "ett": 1,
    "två": 2,
    "tre": 3,
    "fyra": 4,
    "fem": 5,
    "sex": 6,
    "sju": 7,
    "åtta": 8,
    "nio": 9,
    "tio": 10,
    "elva": 11,
    "tolv": 12,
    "arton": 18,
    "tjugofyra": 24,
    "trettiosex": 36,
    "fyrtioåtta": 48,
}
_WORD = "|".join(sorted(_NUMBER_WORDS, key=len, reverse=True))
_NUMBER = rf"(?P<number>\d{{1,3}}|[a-zåäö]+\s*\(\d{{1,3}}\)|(?:{_WORD}))"
_UNIT = r"(?P<unit>månader|månad|år)\b"

# P1: 76dfb5d1ae1f p1 "Avtalsnamn IT-drift 2023, område Större | Startdatum 2024-11-14 ⏎
# Ref. nr. 23.3-10639-2023 | Slutdatum 2028-11-13 ⏎ | Förlängning Ingen förlängning".
# Other cells can stand between the labels (f479352f0a55: "Startdatum | 2023-11-01 ⏎ |
# Systemutveckling | Slutdatum"), hence the 80 characters. "Förlängning" is read only
# after "Slutdatum": on its own, "Förlängning X" gave 21 false hits ("Förlängning
# regleras skriftligen").
_COVER = re.compile(
    rf"Startdatum{_SEP}(?P<start>{_DATE}).{{0,80}}?Slutdatum{_SEP}(?P<end>{_DATE})"
    rf"(?:.{{0,60}}?Förlängning{_SEP}(?P<none>Ingen{_SEP}förlängning))?",
    re.DOTALL,
)
# P2: 185872a6bb90 §1.8 "Ramavtalet blir bindande från och med det datumet som Parterna
# signerar det men träder i kraft 2023-02-27"; with "per" in 4b6c2a533fae b16 "när nya AO5
# träder i kraft per 2025-08-23".
_IN_FORCE = re.compile(rf"träder (?:Ramavtalet )?i ?kraft (?:den |per )?(?P<day>{_DATE})", re.I)
# P2 counts only when the agreement is what enters into force: named in the sentence before
# the match or in it ("träder Ramavtalet i kraft"). All 13 pilot hits name it; "De justerade
# priserna träder i kraft D" (a price change) and "Tilläggsavtalet träder i kraft D" (an
# amendment's own start) do not.
_AGREEMENT = re.compile(r"\b(?:ramavtalet|volymavtalet|avtalet|AO\d+)\b", re.I)
# P3: 185872a6bb90 §1.8 "Ramavtalet löper under en period av 48 månader, dock längst till
# och med den 2027-02-26." "längst" is required: a bare "till och med D" is mostly the
# validity of a bid or an extended deadline (25 of 41 hits).
_LATEST_END = re.compile(rf"längst till och med\s*(?:den )?(?P<day>{_DATE})", re.I)
# P4: 14aa1cc8ee3d §9.6.2 "den dag det signerats av båda Parter samt tidigast från och med
# den 2022-12-01". "tidigast" is required: 34d71a7e4da0 §8.10.2 "fasta i ett (1) år från
# och med 2025-04-03" is a price lock.
_EARLIEST_START = re.compile(rf"tidigast från och med (?:den )?(?P<day>{_DATE})", re.I)
# P5: 4b6c2a533fae §2.4 "Ramavtalet för område 3 - IT-säkerhet är giltigt från och med
# 2026-03-10 och till och med 2030-03-09"; c27b833f338d §2.1 "Volymavtalet gäller under tre
# år med start från 2024-05-01 och slutar 2027-04-30".
_RANGE_IN_WORDS = re.compile(
    rf"(?:giltig[at]?|gäller) från och med (?:den )?(?P<start>{_DATE})\s*(?:och )?"
    rf"till och med (?:den )?(?P<end>{_DATE})"
    rf"|med start från (?P<start2>{_DATE}) och slutar (?P<end2>{_DATE})",
    re.I,
)
# P6: 171a3cacf5fd footer "för avtalsperiod 2024-05-01 - 2027-04-30" and the rows of
# 49f36699a469 b83. The space before the dash can be missing: 4b6c2a533fae b16
# "avtalstiden (2026-03-10- 2030-03-09)".
_RANGE = re.compile(rf"(?P<start>{_DATE})\s*[-–]\s*(?P<end>{_DATE})")
# P6 counts only after a period label in the same sentence (the footer and 4b6c2a533fae
# above), or in a table whose header row has one (49f36699a469 "Ramavtalområde |
# Avtalsperiod"). A bare range is otherwise any period: "Prislistan gäller 2025-01-01 –
# 2025-12-31".
_PERIOD_LABEL = re.compile(r"avtalsperiod|avtalstid", re.I)
# P7: 185872a6bb90 §1.8 "Ramavtalet löper under en period av 48 månader"; 14aa1cc8ee3d
# §9.6.2 "Från 2022-12-01 löper ramavtalet därefter under en period av 24 månader".
# "löper" is required: "säga upp Ramavtalet tidigast 24 månader innan Ramavtalet löper ut"
# (185872a6bb90 §1.8) is a notice period.
_LENGTH = re.compile(
    rf"löper (?:ramavtalet )?(?:därefter )?(?:under en period av |i )(?:högst )?{_NUMBER} {_UNIT}",
    re.I,
)
# P8: 14aa1cc8ee3d §9.6.3 "kan en eller flera förlängningar av Ramavtalets giltighetstid
# uppgå till maximalt 24 månader". Within one sentence (no full stop between).
_EXTENSION = re.compile(
    rf"förlängning(?:ar)?\b[^.]{{0,120}}?(?:uppgå till |om |med )(?:högst |maximalt )?"
    rf"{_NUMBER} {_UNIT}",
    re.I,
)
# Any length, for the longest one after a P8 match: "förlängning med 12 månader i taget,
# dock maximalt 24 månader totalt" allows 24 months, not 12.
_ANY_LENGTH = re.compile(rf"\b{_NUMBER} {_UNIT}", re.I)
# P9: c59dbfeeb576 §1.8 "Ramavtalet beräknas träda i kraft 2025-04-03, om upphandlingen
# inte blir föremål för överprövning". A plan: 8 of 14 differ from the register.
_PLANNED = re.compile(rf"beräknas träda i kraft (?:tidigast )?(?:den )?(?P<day>{_DATE})", re.I)
# P10: 34d71a7e4da0 §8.7 "träder i kraft den [DATUM (dag-mån-år)]", 37f6a4caa617 §2.6
# "giltigt till och med ÅÅ-MM-DD", 5b38873c2b7a §1.1 "börjar gälla den 20xx-xx-xx",
# a16f04246875 "påbörjas den insert date". An unfilled agreement number
# ("23.3-8321-2024-XXX") is no date field; `identifiers.py` finds it.
_DATE_FIELD = re.compile(r"\[DATUM[^\]]*\]|ÅÅ(?:ÅÅ)?-MM-DD|20xx-xx-xx|\binsert date\b", re.I)
# P11: 185872a6bb90 p18 "<signer id> 2023-02-22 15:14". Only the date and time are kept:
# the block also holds the signer's id, and the block before it the signer's name.
_SIGNATURE = re.compile(rf"(?P<day>{_DATE}) \d{{2}}:\d{{2}}(?!\d)")

# A sub-area named in the text: "område 3 - IT-säkerhet" (up to "är", a comma or the end
# of the sentence) or the area code "AO5" of 4b6c2a533fae's version table.
_AREA = re.compile(r"\bområde \d+ [-–] [^,.;|]+?(?= är\b| gäller\b|[,.;|]|$)|\bAO\d+\b")

_COVER_WINDOW = 3  # blocks P1 reads at a time, see "Windows" above
_PERIOD_KINDS = (
    FactKind.PERIOD_START,
    FactKind.PERIOD_END,
    FactKind.PERIOD_MONTHS,
    FactKind.EXTENSION_MONTHS,
)


@dataclass(frozen=True)
class _Found:
    """A fact before it gets its block, statement and scope."""

    kind: FactKind
    value: str
    raw: str
    rule: str
    offset: int  # where the match starts in the block's normalised text


def find_dates(blocks: Sequence[Block]) -> list[Fact]:
    """The period, extension, planned start, signature and date-field facts, in block order."""
    certificate = signature_certificate_page(blocks)
    texts = [_normalise(block.text) for block in blocks]
    statements: dict[tuple[int, int, str | None], int] = {}
    facts: list[Fact] = []
    for index, block in enumerate(blocks):
        text = texts[index]
        found = [*_cover(texts, index), *_clause_facts(text, block.kind is BlockKind.TABLE)]
        if certificate is not None and block.page is not None and block.page >= certificate:
            found += _signatures(text)
        for item in sorted(found, key=lambda item: item.offset):
            statement = scope = None
            if item.kind in _PERIOD_KINDS:
                table = block.kind is BlockKind.TABLE
                row = text.count("\n", 0, item.offset) if table else 0
                scope = _scope(text, item, table)
                statement = statements.setdefault((index, row, scope), len(statements) + 1)
            facts.append(
                Fact(
                    kind=item.kind,
                    value=item.value,
                    raw=item.raw,
                    rule=item.rule,
                    block=index,
                    page=block.page,
                    scope=scope,
                    statement=statement,
                )
            )
    return facts


def parse_period(text: str) -> tuple[date, date] | None:
    """The start and end of a period written "2024-11-14 - 2028-11-13", as on agreement pages.

    None when the text is not one such range, a date does not exist, or the
    period ends before it starts.
    """
    match = _RANGE.fullmatch(text.strip())
    if match is None:
        return None
    start, end = _iso(match["start"]), _iso(match["end"])
    if start is None or end is None or end < start:
        return None
    return start, end


def _cover(texts: Sequence[str], index: int) -> Iterator[_Found]:
    """P1 over this block and the next ones, for the matches that start in this block."""
    window = "\n".join(texts[index : index + _COVER_WINDOW])
    for match in _COVER.finditer(window):
        if match.start() >= len(texts[index]):
            break  # starts in a later block, which is read on its own turn
        start, end = _iso(match["start"]), _iso(match["end"])
        if start is None or end is None:
            continue
        raw = _raw(match)
        yield _Found(FactKind.PERIOD_START, start.isoformat(), raw, "P1", match.start())
        yield _Found(FactKind.PERIOD_END, end.isoformat(), raw, "P1", match.start())
        if match["none"] is not None:
            yield _Found(FactKind.EXTENSION_MONTHS, "0", raw, "P1", match.start())


def _clause_facts(text: str, table: bool = False) -> Iterator[_Found]:
    """P2-P10 in one block's text; `table` when the block is a table."""
    for pattern, kind, rule in (
        (_IN_FORCE, FactKind.PERIOD_START, "P2"),
        (_LATEST_END, FactKind.PERIOD_END, "P3"),
        (_EARLIEST_START, FactKind.PERIOD_START, "P4"),
        (_PLANNED, FactKind.PLANNED_START, "P9"),
    ):
        for match in pattern.finditer(text):
            if pattern is _IN_FORCE and not _of_the_agreement(text, match):
                continue  # something else enters into force
            if (day := _iso(match["day"])) is not None:
                yield _Found(kind, day.isoformat(), _raw(match), rule, match.start())
    for pattern, rule in ((_RANGE_IN_WORDS, "P5"), (_RANGE, "P6")):
        for match in pattern.finditer(text):
            if pattern is _RANGE and not _period_label(text, match, table):
                continue  # a range of something else
            start = _iso(match["start"] or match.groupdict().get("start2"))
            end = _iso(match["end"] or match.groupdict().get("end2"))
            if start is not None and end is not None:
                raw = _raw(match)
                yield _Found(FactKind.PERIOD_START, start.isoformat(), raw, rule, match.start())
                yield _Found(FactKind.PERIOD_END, end.isoformat(), raw, rule, match.start())
    for match in _LENGTH.finditer(text):
        months = _months(match["number"], match["unit"])
        yield _Found(FactKind.PERIOD_MONTHS, str(months), _raw(match), "P7", match.start())
    for match in _EXTENSION.finditer(text):
        yield _longest_extension(text, match)
    for match in _DATE_FIELD.finditer(text):
        yield _Found(FactKind.PLACEHOLDER, "date", match.group(), "P10", match.start())


def _sentence(text: str, match: re.Match[str]) -> int:
    """Where the sentence or table row of a match starts."""
    return max(text.rfind(".", 0, match.start()), text.rfind("\n", 0, match.start())) + 1


def _of_the_agreement(text: str, match: re.Match[str]) -> bool:
    """Whether a P2 match is said of the agreement (see `_AGREEMENT`)."""
    return _AGREEMENT.search(text, _sentence(text, match), match.end()) is not None


def _period_label(text: str, match: re.Match[str], table: bool) -> bool:
    """Whether a P6 range has a period label (see `_PERIOD_LABEL`)."""
    if _PERIOD_LABEL.search(text, _sentence(text, match), match.start()):
        return True
    return table and _PERIOD_LABEL.search(text.split("\n", 1)[0]) is not None


def _longest_extension(text: str, match: re.Match[str]) -> _Found:
    """P8: the longest length from the one matched to the end of the sentence."""
    end = text.find(".", match.end())
    lengths = _ANY_LENGTH.finditer(text, match.start("number"), len(text) if end == -1 else end)
    longest = max(lengths, key=lambda length: _months(length["number"], length["unit"]))
    months = _months(longest["number"], longest["unit"])
    raw = " ".join(text[match.start() : longest.end()].split())
    return _Found(FactKind.EXTENSION_MONTHS, str(months), raw, "P8", match.start())


def _signatures(text: str) -> Iterator[_Found]:
    """P11: only the date and time, never the text around them (a signer's id or name)."""
    for match in _SIGNATURE.finditer(text):
        if (day := _iso(match["day"])) is not None:
            yield _Found(FactKind.SIGNED_ON, day.isoformat(), match.group(), "P11", match.start())


def _scope(text: str, item: _Found, table: bool) -> str | None:
    """The sub-area of a period fact (see "Scope" in the module docstring)."""
    start = text.rfind("\n", 0, item.offset) + 1 if table else 0
    end = text.find("\n", item.offset) if table else -1
    line = text[start:] if end == -1 else text[start:end]
    named = _AREA.findall(line, 0, item.offset - start)
    if named:
        return str(named[-1]).strip()
    if item.rule != "P6" or not table:
        return None
    label = line.split(CELL_SEPARATOR.strip())[0].strip()
    return label if label and _RANGE.search(label) is None else None


def _months(number: str, unit: str) -> int:
    """'48', 'fyrtioåtta (48)' or 'tre', with 'månader' or 'år', as a number of months."""
    digits = re.search(r"\d+", number)
    count = int(digits.group()) if digits else _NUMBER_WORDS[number.lower()]
    return count * 12 if unit.lower() == "år" else count


def _iso(text: str | None) -> date | None:
    if text is None:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None  # a date that does not exist: "2023-02-30"


def _normalise(text: str) -> str:
    """The text with the spaces of each line collapsed; the line breaks of a table stay."""
    return "\n".join(" ".join(line.split()) for line in text.strip().split("\n"))


def _raw(match: re.Match[str]) -> str:
    return " ".join(match.group().split())
