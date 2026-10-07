"""The register-facts rule: the register's values in an answer are the register's own.

What:
    `check_register_facts(draft, entries, sections, user_texts, today)`
    checks the agreement numbers, procurement numbers, organisation numbers
    and dates in a `FinalAnswer`'s text against the register rows of the
    agreements the draft declares in `register_facts`, and gives a
    `RegisterReport`: the problems, in Swedish for the model, the rows as
    `RegisterFact`s for the user, and the values a reservation must name.

Why:
    Which supplier has which agreement, and until when, is answered by the
    register (architecture plan, section 4), and a number or a date one
    digit off looks as right as the true one. The citation check cannot see
    such a value, since nothing quotes it. So the model declares which
    agreements its register facts come from, the chain reads their rows
    again through avtal-mcp (never from the message history, which a client
    sends), and every such value in the text must be one of them, or stand
    in a section whose citation passed or in the user's own words, or be
    today. The rule is deterministic and judges values only: whether a date
    is the start or the end, which sub-area, how many suppliers and what is
    missing are for the reviewer (`review.py`, ADR 0015).

How:
    The text is scanned for values. Agreement numbers first: a case number
    of `CASE_NUMBER_PATTERN` with the supplier's sequence, kept when
    `parse_agreement_reference` accepts it and compared by key (-001 and
    -01 are one agreement); they are then masked. A case number without a
    sequence is a procurement (or an agreement with a single supplier); one
    followed by digits that are no sequence ("-0021", a digit too many) is
    no agreement number, a problem, and not read as the procurement. A short
    form ("(-005)", "till -008") is the agreement of that sequence under the
    last case number before it in its sentence; with none before it, it is
    not read. A range ("23.3-8321-2024-001 till -008", "-001–008") names
    every agreement between its ends. Swedish organisation numbers by their
    shape, NNNNNN-NNNN and SE…01, without the check-digit filter of
    `find_org_numbers`, so a typo is found and named ("har fel
    kontrollsiffra"). Dates as ÅÅÅÅ-MM-DD and as "31 mars 2025", each a day
    of the calendar or a problem. An organisation number or a date with a
    digit too many or too few has another shape and is not read. Section
    numbers ("6.21.9"), ranges of them ("23.1-23.12", and "23.1.10–23.1.12":
    a dotted case number with a serial of at most two digits), prices,
    phone numbers and a year alone have none of these shapes. The passed
    sections (title, file title and text) and the user's texts are scanned
    the same way, so a value backs another written in either form. A value
    is backed by a field of a declared agreement's rows, by a passed
    section, by the user's texts, or, for a date, by being today. A sentence
    ends at . ! or ? before a capital letter, or at a line break; not after
    an abbreviation or a number of one or two digits ("IT-konsulttjänster 3.
    IT-säkerhet"). In a sentence that names declared agreements (by number,
    short form, range, supplier name or former name), a date or an
    organisation number must belong to those agreements' rows, unless today
    backs it, or a section or the user does and the register does not give
    it to another declared agreement: "23.3-5890-2023-002,
    organisationsnummer 556271-9129" is wrong although 556271-9129 is Nordlo
    Improve AB's, declared too, and a cited price list that has it does not
    make it right. A date nothing backs is still backed when it follows,
    give or take a day, from a backed date in the text or from today by an
    offset its sentence writes ("tre månader före 2027-02-17", "tjugofyra
    (24) månader"): the calculation is shown, and the reviewer judges it.
    But a date a day off a date the register has for the agreements of its
    sentence (for all declared ones when it names none) is a near miss, not
    a calculation: "i fyra år, 2024-11-14–2028-11-14" where the register
    says 2028-11-13. Each declared number must be an agreement number the
    register has (`entries` not None) and be used: the text has its number
    (in full, as a short form or within a range), its organisation number,
    or its supplier name or a former name, with or without the legal form
    and in the genitive ("Telia Cygate" and "Telia Cygates" for "Telia
    Cygate AB"). Problems come in the order of `register_facts`, then of
    the text, each once; `unbacked` holds each value once, as first written
    (a short form as the number it stands for).
"""

import calendar
import re
from bisect import bisect_left, bisect_right
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from avtalsagent.agent.register_reader import RegisterEntry
from avtalsagent.agent.schemas import FinalAnswer, RegisterFact
from avtalsagent.agent.sections import CitedSection
from avtalsagent.domain.identifiers import (
    CASE_NUMBER_PATTERN,
    SEQUENCE_PATTERN,
    SEQUENCE_SEPARATOR,
    IdentifierError,
    agreement_key,
    luhn_valid,
    parse_agreement_reference,
    procurement_key,
)

Kind = Literal["agreement", "procurement", "org", "date"]
# A value as the rule compares it: its kind and its key (see `_Value.key`).
Fact = tuple[Kind, str]

# A case number, with the supplier's sequence when one follows, or with digits that are no
# sequence ("-0021", "-1002": `excess`), which make it no agreement number rather than the
# bare procurement. Not after a digit or a dot, so "123.3-…" and "1.23.3-…" are not read; a
# variant letter ("-003-A") only when no letter follows it, so "-002-Avtalet" keeps "-002".
_NUMBER = re.compile(
    rf"(?<![\d.])(?P<case>{CASE_NUMBER_PATTERN})"
    rf"(?:{SEQUENCE_SEPARATOR}(?:{SEQUENCE_PATTERN}|(?P<excess>\d+)))?"
    r"(?!\d|(?<=-[A-Z])[^\W\d_])"
)
# A dotted case number has a long serial ("23.3.2940-20"); with a dot and at most this many
# digits it is a range of sections ("23.1.10–23.1.12", "23.4.11-13").
_SECTION_SERIAL_DIGITS = 2
# A supplier's sequence alone ("(-005)", "till –008"), read by the case number before it in
# its sentence. Three digits, not after a letter, a digit or a separator and not before one,
# so "08-700", "-10 %", "-12,5" and "-100-200" are not read.
_SHORT_SEQUENCE = re.compile(r"(?<![\w.:\-–])[-–](?P<sequence>\d{3})(?![\w\-–:]|[.,]\d)")
# What stands between the ends of a range of agreements: "-001 till -008", "-001–008".
_RANGE = re.compile(r"(?:\s+(?:till(?:\s+och\s+med)?|t\.o\.m\.)\s+)?", re.IGNORECASE)
# A Swedish VAT number ("SE556486168901", "SE 556486-1689 01"), read before the plain form.
_VAT_NUMBER = re.compile(r"(?<!\w)SE\s?(?P<a>\d{6})-?(?P<b>\d{4})\s?01(?!\d)")
# An organisation number by its shape, as `identifiers._ORG_NUMBER_IN_TEXT` reads it but only
# with the hyphen: ten digits in a row are as often a phone number.
_ORG_NUMBER = re.compile(r"(?<![\d\-+])(?P<a>\d{6})\s?[-–]\s?(?P<b>\d{4})(?![\d\-])")
_ISO_DATE = re.compile(r"(?<!\d)(?P<y>(?:19|20)\d{2})-(?P<m>\d{2})-(?P<d>\d{2})(?!\d)")
_MONTHS = (
    "januari",
    "februari",
    "mars",
    "april",
    "maj",
    "juni",
    "juli",
    "augusti",
    "september",
    "oktober",
    "november",
    "december",
)
# "31 mars 2025", "1:a februari 2023".
_LONG_DATE = re.compile(
    rf"(?<![\d.])(?P<d>\d{{1,2}})(?::[ae])?\s+(?P<m>{'|'.join(_MONTHS)})\s+"
    r"(?P<y>(?:19|20)\d{2})(?!\d)",
    re.IGNORECASE,
)
# The hyphen, the non-breaking hyphen and the minus sign as "-": a model may write them, and
# one character for one keeps the positions.
_HYPHENS = str.maketrans(dict.fromkeys("\u2010\u2011\u2212", "-"))

# An offset a computed date is written with: "tre (3) månader", "24 månaders", "30 dagar".
# Digits in parentheses are the amount whatever word is before them: "tjugofyra (24)
# månader", "fjorton (14) dagar".
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
    "femton": 15,
    "tjugo": 20,
    "trettio": 30,
    "fyrtio": 40,
    "femtio": 50,
    "sextio": 60,
    "nittio": 90,
}
_OFFSET = re.compile(
    rf"(?<![\w.,])(?:(?P<n>\d{{1,3}}|{'|'.join(_NUMBER_WORDS)})|[^\W\d_]+)"
    r"(?:\s*\((?P<p>\d{1,3})\))?\s+"
    r"(?P<unit>dag(?:ar|ars|s)?|veck(?:a|as|or|ors)|månad(?:er|ers|s)?|år(?:s)?)(?!\w)",
    re.IGNORECASE,
)
_UNITS = ("dag", "veck", "månad", "år")  # the stems `_shift` moves by
_MONTHS_PER_YEAR = 12

# A sentence ends at . ! or ? before a capital letter, or at a line break. Not after a
# number of one or two digits, which the register's sub-areas have ("IT-konsulttjänster 3.
# IT-säkerhet"), and not after an abbreviation: "f.d. EPM Data" and "org.nr. 556486-1689"
# are one sentence. A line break is tried once per run of white space, not at each of its
# characters, so a long run costs no more than its length.
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])(?<!\s\d\.)(?<!\s\d\d\.)\s+(?=[A-ZÅÄÖ])|(?<!\s)\s*\n\s*")
_ABBREVIATION = re.compile(
    r"(?:^|[\s(])(?:bl\.a|ca|dvs|d\.v\.s|enl|exkl|f\.d|inkl|jfr|kap|m\.m|nr|org\.nr|p|resp"
    r"|s\.k|st|t\.ex)\.$",
    re.IGNORECASE,
)
_ABBREVIATION_REACH = 12  # characters before a break that may hold one

# A supplier's legal form, which the text may leave out: "Telia Cygate" for "Telia Cygate AB".
_LEGAL_FORM = re.compile(
    r"^(?:AB|Aktiebolaget)\s+"
    r"|\s+(?:AB|Aktiebolag|Oy|Oyj|AS|A/S|ApS|Ltd\.?|Limited|GmbH|Inc\.?|B\.?V\.?)"
    r"(?:\s*\(publ\))?$"
    r"|\s*\(publ\)$"
)
_MIN_NAME_LENGTH = 3  # a shorter name would be found inside ordinary words
# A problem lists at most this many agreements; more are "avtalen i register_facts".
_MAX_LISTED = 3


@dataclass(frozen=True)
class RegisterReport:
    """The outcome of the rule: no problems means the register facts passed."""

    problems: list[str]  # in Swedish, for the model: each names its value
    facts: list[RegisterFact]  # every row of each declared agreement the register has
    unbacked: list[str]  # the values the user's reservation note names, as written


@dataclass(frozen=True)
class _Value:
    """A register-shaped value in a text."""

    kind: Kind
    # Agreement or procurement key, NNNNNN-NNNN, or the ISO date; as written for a date
    # that is no day of the calendar.
    key: str
    raw: str  # as written; for a short form ("-008"), the number it stands for
    start: int
    procurement: str | None = None  # an agreement number's procurement key
    # An organisation number's check digit is right; an agreement number's sequence has two
    # or three digits.
    valid: bool = True
    day: date | None = None  # a date's day; None when the calendar has no such day
    # A range's end ("-001 till -008"): the keys of the agreements between its two ends.
    covers: frozenset[str] = frozenset()

    @property
    def fact(self) -> Fact:
        return (self.kind, self.key)


@dataclass(frozen=True)
class _Agreement:
    """A declared agreement the register has."""

    number: str  # as declared
    key: str
    rows: list[RegisterEntry]
    facts: frozenset[Fact]  # the values of its rows
    names: re.Pattern[str] | None  # its supplier and former names


def check_register_facts(
    draft: FinalAnswer,
    entries: Mapping[str, list[RegisterEntry] | None],
    sections: Sequence[CitedSection],
    user_texts: Sequence[str],
    today: date,
) -> RegisterReport:
    """Check `draft`'s register values against the rows of its declared agreements.

    `entries` is keyed by each number as written in `draft.register_facts`;
    None (or a missing key) is an agreement the register does not have or
    that could not be read. `sections` are the sections whose citations
    passed; `user_texts` the question and the user's answers to `ask_user`.
    """
    problems: dict[str, None] = {}
    unbacked: dict[Fact, str] = {}
    agreements: list[_Agreement] = []
    lacking: set[str] = set()  # keys of declared numbers the register does not have
    for number in draft.register_facts:
        key = agreement_key(number)
        if key is None:
            problems[
                f"Avtalsnumret {number} i register_facts är inget avtalsnummer. Kopiera numret "
                "från search_register, eller ta bort det ur register_facts."
            ] = None
            unbacked.setdefault(("agreement", number.strip()), number)
            continue
        if key in lacking or any(agreement.key == key for agreement in agreements):
            continue  # another spelling of an agreement already declared
        rows = entries.get(number)
        if not rows:
            problems[
                f"Avtalsnumret {number} i register_facts finns inte i registret. Kopiera numret "
                "från search_register, eller ta bort det ur register_facts."
            ] = None
            unbacked.setdefault(("agreement", key), number)
            lacking.add(key)
            continue
        agreements.append(_agreement(number, key, rows))

    values = _scan(draft.text)
    for agreement in agreements:
        if not _used(agreement, draft.text, values):
            problems[
                f"Avtalet {agreement.number} i register_facts används inte i texten. Ta bort det "
                "ur register_facts om texten inte tar några uppgifter om det ur registret."
            ] = None

    elsewhere = _facts(_scan(part) for part in _section_parts(sections))
    elsewhere |= _facts(_scan(user_text) for user_text in user_texts)
    for value, named in _unbacked(draft.text, values, agreements, elsewhere, today):
        if value.kind != "date" and value.key in lacking:
            continue  # a number the register lacks: its declaration's problem says so
        problems[_problem(value, named, agreements)] = None
        unbacked.setdefault(value.fact, value.raw)

    return RegisterReport(
        problems=list(problems),
        facts=[_fact(row) for agreement in agreements for row in agreement.rows],
        unbacked=list(unbacked.values()),
    )


# --- the scan -----------------------------------------------------------------------


def _scan(text: str) -> list[_Value]:
    """The register-shaped values in `text`, in the order they are written."""
    text = text.translate(_HYPHENS)
    found: list[_Value] = []
    chars = list(text)

    def mask(match: re.Match[str]) -> str:
        """`match`'s text as written; it is blanked out for the scans after this one."""
        chars[match.start() : match.end()] = " " * (match.end() - match.start())
        return text[match.start() : match.end()]

    # The case numbers read, as (start, end, procurement key, sequence), for the short forms.
    cases: list[tuple[int, int, str, int | None]] = []
    for match in _NUMBER.finditer(text):
        if match["sep1"] == "." and len(match["serial"]) <= _SECTION_SERIAL_DIGITS:
            continue  # a range of sections
        excess = match["excess"]
        try:
            reference = parse_agreement_reference(match["case"] if excess else match.group())
        except IdentifierError:  # a section number or a range of them
            continue
        procurement = reference.procurement.key
        if excess is not None:
            key = f"{procurement}-{excess}"
            found.append(_Value("agreement", key, mask(match), match.start(), valid=False))
            cases.append((match.start(), match.end(), procurement, None))
            continue
        kind: Kind = "procurement" if reference.sequence is None else "agreement"
        found.append(_Value(kind, reference.key, mask(match), match.start(), procurement))
        sequence = reference.sequence
        number = None if sequence is None else int(sequence.partition("-")[0])  # not "-A"
        cases.append((match.start(), match.end(), procurement, number))
    found += _short_forms(text, "".join(chars), cases, mask)
    for pattern in (_VAT_NUMBER, _ORG_NUMBER):
        for match in pattern.finditer("".join(chars)):
            org = f"{match['a']}-{match['b']}"
            found.append(_Value("org", org, mask(match), match.start(), valid=luhn_valid(org)))
    for match in _ISO_DATE.finditer("".join(chars)):
        found.append(_date(match, int(match["m"]), mask(match)))
    for match in _LONG_DATE.finditer("".join(chars)):
        found.append(_date(match, _MONTHS.index(match["m"].lower()) + 1, mask(match)))
    return sorted(found, key=lambda value: value.start)


def _short_forms(
    text: str,
    masked: str,
    cases: list[tuple[int, int, str, int | None]],
    mask: Callable[[re.Match[str]], str],
) -> list[_Value]:
    """The short forms of `text` ("-008"), each read by the last case number before it.

    `masked` is `text` with its case numbers blanked out and `cases` are those numbers, in
    order. A short form with no case number before it in its sentence is not read. One that
    is read is the last case number for the next, so "-001 till -008" is a range too.
    """
    found = []
    starts = [start for start, _ in _sentences(text)]
    following = iter(cases)
    upcoming = next(following, None)
    previous: tuple[int, int, str, int | None] | None = None
    for match in _SHORT_SEQUENCE.finditer(masked):
        while upcoming is not None and upcoming[0] < match.start():
            previous, upcoming = upcoming, next(following, None)
        sentence = starts[bisect_right(starts, match.start()) - 1]
        if previous is None or previous[0] < sentence:
            continue  # an ordinary number, or nothing to read it by
        _, end, procurement, first = previous
        last = int(match["sequence"])
        key = f"{procurement}-{match['sequence']}"
        covers: frozenset[str] = frozenset()
        if first is not None and first < last and _RANGE.fullmatch(text, end, match.start()):
            covers = frozenset(f"{procurement}-{between:03d}" for between in range(first + 1, last))
        mask(match)
        found.append(_Value("agreement", key, key, match.start(), procurement, covers=covers))
        previous = (match.start(), match.end(), procurement, last)
    return found


def _date(match: re.Match[str], month: int, raw: str) -> _Value:
    """A date value; one the calendar does not have keeps its spelling as its key."""
    try:
        day = date(int(match["y"]), month, int(match["d"]))
    except ValueError:
        return _Value("date", " ".join(raw.casefold().split()), raw, match.start())
    return _Value("date", day.isoformat(), raw, match.start(), day=day)


def _facts(scans: Iterable[list[_Value]]) -> set[Fact]:
    """The values of scanned texts; an agreement number also gives its procurement."""
    facts: set[Fact] = set()
    for values in scans:
        for value in values:
            facts.add(value.fact)
            if value.procurement is not None:
                facts.add(("procurement", value.procurement))
    return facts


def _section_parts(sections: Sequence[CitedSection]) -> Iterable[str]:
    for section in sections:
        yield from (section.section_title, section.file_title, section.text)


# --- the declared agreements ---------------------------------------------------------


def _agreement(number: str, key: str, rows: list[RegisterEntry]) -> _Agreement:
    facts: set[Fact] = set()
    for row in rows:
        facts.add(("agreement", agreement_key(row.agreement_number) or row.agreement_number))
        procurement = procurement_key(row.procurement_number) or row.procurement_number
        facts.add(("procurement", procurement))
        facts.add(("org", row.org_number))
        for day in (row.valid_from, row.valid_to, row.max_extension_to):
            if day is not None:
                facts.add(("date", day.isoformat()))
    return _Agreement(number, key, rows, frozenset(facts), _name_pattern(rows))


def _name_pattern(rows: Sequence[RegisterEntry]) -> re.Pattern[str] | None:
    """The supplier's names and former names, with and without the legal form.

    A name in the genitive is the name too: "Telia Cygates", "Nordlo Advance AB:s".
    """
    names: set[str] = set()
    for row in rows:
        for name in (row.supplier_name, *row.former_names):
            full = " ".join(name.split())
            names.update(
                variant
                for variant in (full, _LEGAL_FORM.sub("", full).strip())
                if len(variant) >= _MIN_NAME_LENGTH
            )
    if not names:
        return None
    longest_first = sorted(names, key=lambda name: (-len(name), name))
    alternatives = "|".join(re.escape(name) for name in longest_first)
    return re.compile(rf"(?<!\w)(?:{alternatives})(?::?s)?(?!\w)", re.IGNORECASE)


def _keys(values: Iterable[_Value]) -> set[str]:
    """The agreement and procurement keys of `values`, with every agreement a range covers."""
    keys: set[str] = set()
    for value in values:
        if value.kind in ("agreement", "procurement"):
            keys.add(value.key)
            keys |= value.covers
    return keys


def _names(agreement: _Agreement, sentence: str, keys: set[str]) -> bool:
    """True when `sentence` names the agreement: its number, or its supplier by name."""
    if agreement.key in keys:
        return True
    return agreement.names is not None and agreement.names.search(sentence) is not None


def _used(agreement: _Agreement, text: str, values: Sequence[_Value]) -> bool:
    """True when the text has the agreement's number, organisation number or a name.

    A number may be a short form after a case number ("-005") or within a range ("-001 till
    -008").
    """
    if _names(agreement, text, _keys(values)):
        return True
    orgs = {value.key for value in values if value.kind == "org"}
    if any(row.org_number in orgs for row in agreement.rows):
        return True
    # A foreign organisation number, or an old number ("6765/05"), is not scanned for.
    folded = text.casefold()
    spellings = {agreement.number.strip()}
    for row in agreement.rows:
        spellings |= {row.agreement_number, row.org_number}
    return any(spelling.casefold() in folded for spelling in spellings if spelling)


def _fact(row: RegisterEntry) -> RegisterFact:
    return RegisterFact(
        agreement_number=row.agreement_number,
        supplier_name=row.supplier_name,
        former_names=row.former_names,
        org_number=row.org_number,
        sub_area=row.sub_area,
        valid_from=row.valid_from,
        valid_to=row.valid_to,
        max_extension_to=row.max_extension_to,
    )


# --- the judgement ------------------------------------------------------------------


def _unbacked(
    text: str,
    values: Sequence[_Value],
    agreements: Sequence[_Agreement],
    elsewhere: set[Fact],
    today: date,
) -> list[tuple[_Value, list[_Agreement]]]:
    """The values of `text` nothing backs, each with the agreements its sentence names."""
    registered = frozenset[Fact]().union(*(agreement.facts for agreement in agreements))
    # Each value with the agreements its sentence names, whether it is backed, whether it
    # may be computed, and the offsets its sentence writes.
    judged: list[tuple[_Value, list[_Agreement], bool, bool, list[tuple[int, str]]]] = []
    positions = [value.start for value in values]  # in order, as `_scan` gives them
    for start, end in _sentences(text):
        inside = values[bisect_left(positions, start) : bisect_left(positions, end)]
        if not inside:
            continue
        sentence = text[start:end]
        keys = _keys(inside)
        named = [agreement for agreement in agreements if _names(agreement, sentence, keys)]
        paired = frozenset[Fact]().union(*(agreement.facts for agreement in named))
        # What the register gives to another declared agreement than those named, no section
        # or user text backs: "556271-9129" is Nordlo Improve AB's, not Nordlo Advance AB's.
        others = registered - paired if named else frozenset[Fact]()
        # A day off one of the register's dates for the sentence is a near miss, not computed.
        scope = paired if named else registered
        days = [date.fromisoformat(key) for kind, key in scope if kind == "date"]
        offsets = _offsets(sentence)
        for value in inside:
            allowed = paired if named and value.kind in ("org", "date") else registered
            backed = (
                _backed(value, allowed)
                or (value.fact not in others and _backed(value, elsewhere))
                or value.day == today
            )
            near = value.day is not None and any(abs((value.day - d).days) == 1 for d in days)
            judged.append((value, named, backed, not near, offsets))
    # A computed date starts from a date the text has and something backs, or from today.
    bases = {value.day for value, _, backed, _, _ in judged if backed and value.day is not None}
    bases.add(today)
    return [
        (value, named)
        for value, named, backed, computable, offsets in judged
        if not backed
        and not (computable and value.day is not None and _computed(value.day, bases, offsets))
    ]


def _backed(value: _Value, facts: frozenset[Fact] | set[Fact]) -> bool:
    if value.fact in facts:
        return True
    # A case number without a sequence may be an agreement with a single supplier.
    return value.kind == "procurement" and ("agreement", value.key) in facts


def _sentences(text: str) -> list[tuple[int, int]]:
    """The (start, end) of each sentence of `text`."""
    spans = []
    start = 0
    for brk in _SENTENCE_BREAK.finditer(text):
        before = text[max(start, brk.start() - _ABBREVIATION_REACH) : brk.start()]
        if "\n" not in brk.group() and _ABBREVIATION.search(before):
            continue
        spans.append((start, brk.start()))
        start = brk.end()
    spans.append((start, len(text)))
    return spans


def _offsets(sentence: str) -> list[tuple[int, str]]:
    """The offsets a sentence writes, as (amount, unit): (3, "månad") for "tre månader"."""
    found = []
    for match in _OFFSET.finditer(sentence):
        if match["p"] is not None:  # "tjugofyra (24) månader"
            amount = int(match["p"])
        elif match["n"] is not None:
            number = match["n"].lower()
            amount = int(number) if number.isdigit() else _NUMBER_WORDS[number]
        else:
            continue  # a word that is no amount: "flera månader"
        unit = next(unit for unit in _UNITS if match["unit"].lower().startswith(unit))
        if amount > 0:
            found.append((amount, unit))
    return found


def _computed(day: date, bases: set[date], offsets: Sequence[tuple[int, str]]) -> bool:
    """True when `day` is a base date moved by one of the offsets, give or take a day."""
    for base in bases:
        for amount, unit in offsets:
            for sign in (1, -1):
                if abs((day - _shift(base, sign * amount, unit)).days) <= 1:
                    return True
    return False


def _shift(day: date, amount: int, unit: str) -> date:
    """`day` moved by `amount` units; a month's end where the month is shorter."""
    if unit == "dag":
        return day + timedelta(days=amount)
    if unit == "veck":
        return day + timedelta(weeks=amount)
    months = day.month - 1 + amount * (_MONTHS_PER_YEAR if unit == "år" else 1)
    year, month = day.year + months // _MONTHS_PER_YEAR, months % _MONTHS_PER_YEAR + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def _problem(value: _Value, named: Sequence[_Agreement], agreements: Sequence[_Agreement]) -> str:
    """What the model is told about a value nothing backs."""
    if value.kind == "agreement" and not value.valid:
        return (
            f"Avtalsnumret {value.raw} i texten är inget avtalsnummer. Kopiera numret från "
            "search_register, eller rätta det."
        )
    if value.kind == "agreement":
        return (
            f"Avtalsnumret {value.raw} i texten finns inte i register_facts eller i något "
            "citerat avsnitt. Lägg det i register_facts om uppgiften kommer från "
            "search_register, annars rätta det."
        )
    if value.kind == "procurement":
        return (
            f"Numret {value.raw} i texten hör inte till något avtal i register_facts och står "
            "inte i något citerat avsnitt. Lägg avtalsnumret i register_facts om uppgiften "
            "kommer från search_register, annars rätta det."
        )
    if value.kind == "org":
        if not value.valid:
            return (
                f"Organisationsnumret {value.raw} har fel kontrollsiffra. Kopiera det från "
                "search_register."
            )
        if len(named) > _MAX_LISTED:
            return (
                f"Organisationsnumret {value.raw} hör inte till något av avtalen som meningen "
                "nämner. Kopiera rätt nummer från search_register."
            )
        if named:
            orgs = ", ".join(sorted({row.org_number for a in named for row in a.rows}))
            return (
                f"Organisationsnumret {value.raw} hör inte till {_numbers(named)} (registret: "
                f"{orgs}). Kopiera rätt nummer från search_register."
            )
        return (
            f"Organisationsnumret {value.raw} står inte i registret för "
            f"{_which(agreements)} och inte i något citerat avsnitt. Kopiera det från "
            "search_register och lägg avtalsnumret i register_facts, eller ta bort det."
        )
    if value.day is None:
        return f"Datumet {value.raw} finns inte i kalendern. Rätta det."
    scope = named or agreements
    if not scope or len(scope) > _MAX_LISTED:
        where = "avtalen som meningen nämner" if named else _which(agreements)
    else:
        where = ", ".join(f"{a.number} ({_periods(a.rows)})" for a in scope)
    return (
        f"Datumet {value.raw} står inte i registret för {where} och inte i något citerat "
        "avsnitt. Rätta det, eller skriv hur ett beräknat datum räknas."
    )


def _numbers(agreements: Sequence[_Agreement]) -> str:
    return ", ".join(agreement.number for agreement in agreements)


def _which(agreements: Sequence[_Agreement]) -> str:
    """How a problem names the declared agreements as a whole."""
    if not agreements:
        return "något avtal i register_facts"
    if len(agreements) > _MAX_LISTED:
        return "avtalen i register_facts"
    return _numbers(agreements)


def _periods(rows: Sequence[RegisterEntry]) -> str:
    """An agreement's validity as the register has it: "giltigt 2024-11-14–2028-11-13"."""
    periods = sorted(
        {(row.valid_from, row.valid_to, row.max_extension_to) for row in rows},
        key=lambda period: (period[0], period[1], period[2] or date.min),
    )
    described = []
    for valid_from, valid_to, extension in periods:
        text = f"giltigt {valid_from.isoformat()}–{valid_to.isoformat()}"
        if extension is not None and extension != valid_to:
            text += f", kan förlängas till {extension.isoformat()}"
        described.append(text)
    return "; ".join(described)
