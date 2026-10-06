"""Ingestion step 4: case numbers, agreement numbers and organisation numbers.

What:
    `find_identifiers` reads every block of a parsed file and gives one `Fact`
    per case number (PROCUREMENT_NUMBER), agreement number (AGREEMENT_NUMBER),
    organisation number (ORG_NUMBER) and unfilled identifier field
    (PLACEHOLDER). A case or agreement number also gets a role: SELF, the
    document's own number, or CITATION, the number of another agreement.

Why:
    Step 5 holds a document back when its own numbers deviate from the
    register: a case number that is not a procurement of the pages linking to
    it, a supplier card with another agreement number, an organisation number
    in no register row. The case number is mostly in page headers, which step
    3 removes from the sections (3,003 of 3,257 hits; 34 files have it nowhere
    else, M4 survey identifiers.md §0), so the blocks are read, not the
    sections. A document cites other agreements too, and a citation is no
    deviation: 54211e718d8e §1.4: "ramavtal IT-konsulttjänster Resurskonsulter,
    region Södra (dnr 23.3-7067-17) som omfattar länen". In the pilot this is
    the only number in parentheses. The document's own number stands in page
    headers ("Sid 2 (27) Dnr 23.5-1688-2024"), on covers, in "Ramavtal med
    avtalsnummer X" and in running text such as "Detta avrop görs från ramavtal
    ... med diarienummer 23.3-2283-22" (adcd1c5ed90e §2), so the role is read
    from the parentheses alone (`role_of`).

How:
    The rules, each with its id in `Fact.rule`:
    - PROC: Kammarkollegiet's case number, `CASE_NUMBER_PATTERN` in
      `domain/identifiers.py` ("23.3-5890-2023", "23.3.2940-20",
      "23.5-03893-2021"). With a sequence it is an agreement number
      ("23.3.2940-20:026") and gives only an AGREEMENT_NUMBER fact; with an
      unfilled sequence ("23.3-1688-2024:[XXX]") it gives the procurement
      number and a PLACEHOLDER.
    - OLD: an agreement number before the 23.x series ("6765/05"), only right
      after a label.
    - OLDKK: Kammarkollegiet's older letterhead form ("96-15-2015").
    - ORG: a Swedish organisation number with a right check digit
      (`find_org_numbers`), the authority's own 202100-0829 included.
    - PH: an unfilled identifier field after its label ("organisationsnummer
      XXXXXX-XXXX", "avtalsnummer [X]", "dnr: XXX", "Organisationsnummer: |")
      or in brackets ("[diarienr: xxxx]"), and the PROC case above.
    A fact's value is the key of the number ("23.3-2940-2020-018");
    `step4_extract.py` replaces it with the register's spelling when the
    register has the key. A placeholder's value says which field is unfilled:
    "procurement_number", "agreement_number" or "org_number". Case-management
    numbers (23.5, "96-15-2015") are found like the others; step 5 never
    compares them with the register.
"""

import re
from collections.abc import Iterator, Sequence

from avtalsagent.domain.extracted import Fact, FactKind, FactRole
from avtalsagent.domain.identifiers import (
    CASE_NUMBER_PATTERN,
    LETTERHEAD_NUMBER_PATTERN,
    OLD_NUMBER_PATTERN,
    SEQUENCE_PATTERN,
    IdentifierError,
    find_org_numbers,
    is_placeholder,
    parse_agreement_reference,
    parse_procurement_number,
)
from avtalsagent.domain.parsed import Block

# PROC: a case number, not inside a section number or another number, optionally
# followed by a supplier sequence or an unfilled one: "23.3-8321-2024-XXX",
# "23.3-1688-2024:[XXX]", "23.3-2651-2022-X".
_CASE_NUMBER = re.compile(
    rf"(?<![\d.])(?P<number>{CASE_NUMBER_PATTERN})"
    rf"(?:[-:](?:{SEQUENCE_PATTERN}|(?P<blank>\[?[Xx]{{1,4}}\]?))(?!\w))?"
)
# OLD: "kompletteras Volymavtalet för Programvaror, avtalsnummer 6765/05 enligt
# följande" (fb9447f0b8bf §1), "ER BETECKNING ⏎ Avtal 6765/05" (its letterhead). The
# label is required: the bare form has 4 hits in the pilot's blocks, and the fourth is
# "arbetena till LOU (prop. 2015/16:195" (50edddbad6c7).
_OLD_NUMBER = re.compile(
    rf"\b(?:avtal|avtalsnummer|dnr|diarienr)\.?:?\s+(?P<number>{OLD_NUMBER_PATTERN})(?![\d/])",
    re.IGNORECASE,
)
# OLDKK: "DIARIENR ⏎ 2018-05-23 ⏎ ERT DATUM ⏎ 96-15-2015" (fb9447f0b8bf). The letterhead
# puts the label in another cell than the number, so no label is required; the
# form matched nothing else in the pilot.
_LETTERHEAD_NUMBER = re.compile(rf"(?<![\d.\-])(?P<number>{LETTERHEAD_NUMBER_PATTERN})(?![\d\-])")

# The labels of the three identifiers, and which placeholder each gives.
_LABEL = r"(?P<label>avtalsnummer|organisationsnummer|org\.?\s?nr|diarienummer|diarienr|dnr)"
_PLACEHOLDER_KINDS = {
    "avtalsnummer": "agreement_number",
    "organisationsnummer": "org_number",
    "orgnr": "org_number",
    "diarienummer": "procurement_number",
    "diarienr": "procurement_number",
    "dnr": "procurement_number",
}
# PH, in brackets: "8. Avropsförfrågan [diarienr: xxxx] med bilagor" (145e34c51489 §1),
# "[Avropsberättigades organisationsnummer]".
_BRACKETED_FIELD = re.compile(rf"\[[^\[\]\n]{{0,40}}\b{_LABEL}\b[^\[\]\n]{{0,40}}\]", re.I)
# PH, after the label: the text up to the next comma, cell or line is the value; in a
# table, the next cell. "och Leverantör organisationsnummer XXXXXX-XXXX, nedan
# Ramavtalsleverantören" (0692da436391 §1.2.1), "Ramavtal med avtalsnummer [X], har
# träffats" (11db2f3d1852 block 698), "Organisationsnummer: | " (0486216326ec §2).
_LABELLED_FIELD = re.compile(
    rf"\b{_LABEL}\.?(?P<colon>\s*:)?[ \t]*(?:\|[ \t]*)?(?P<value>[^,\n|]{{0,40}})",
    re.IGNORECASE,
)

# What a rule found in a block's text: kind, value, raw, rule, role.
_Found = tuple[FactKind, str, str, str, FactRole | None]


def find_identifiers(blocks: Sequence[Block]) -> list[Fact]:
    """The case, agreement and organisation numbers of a document, in block order."""
    return [
        Fact(kind=kind, value=value, raw=raw, rule=rule, block=index, page=block.page, role=role)
        for index, block in enumerate(blocks)
        for kind, value, raw, rule, role in _identifiers(block.text)
    ]


def role_of(text: str, start: int, end: int) -> FactRole:
    """CITATION when the number at text[start:end] stands in parentheses, else SELF.

    In parentheses means: the last parenthesis before it opens one, and the next
    one after it closes it. A closed pair before the number, as the page counter
    in "Sid 2 (27) Dnr 23.5-1688-2024", does not count.
    """
    before, after = text[:start], text[end:]
    opened = before.rfind("(") > before.rfind(")")
    closing = after.find(")")
    closed = closing != -1 and "(" not in after[:closing]
    return FactRole.CITATION if opened and closed else FactRole.SELF


def _identifiers(text: str) -> Iterator[_Found]:
    yield from _case_numbers(text)
    for match in _OLD_NUMBER.finditer(text):
        key = parse_procurement_number(match["number"]).key
        role = role_of(text, match.start("number"), match.end())
        yield FactKind.PROCUREMENT_NUMBER, key, match["number"], "OLD", role
    for match in _LETTERHEAD_NUMBER.finditer(text):
        key = parse_procurement_number(match["number"]).key
        role = role_of(text, match.start(), match.end())
        yield FactKind.PROCUREMENT_NUMBER, key, match.group(), "OLDKK", role
    for start, end, org_number in find_org_numbers(text):
        yield FactKind.ORG_NUMBER, org_number, text[start:end], "ORG", None
    yield from _placeholders(text)


def _case_numbers(text: str) -> Iterator[_Found]:
    for match in _CASE_NUMBER.finditer(text):
        try:
            number = parse_procurement_number(match["number"])
        except IdentifierError:
            continue  # both separators are dots: a section number
        role = role_of(text, match.start(), match.end())
        if match["sequence"] is not None:
            key = parse_agreement_reference(match.group()).key
            yield FactKind.AGREEMENT_NUMBER, key, match.group(), "PROC", role
            continue
        yield FactKind.PROCUREMENT_NUMBER, number.key, match["number"], "PROC", role
        if match["blank"] is not None:
            yield FactKind.PLACEHOLDER, "agreement_number", match.group(), "PH", None


def _placeholders(text: str) -> Iterator[_Found]:
    bracketed = list(_BRACKETED_FIELD.finditer(text))
    for match in bracketed:
        yield FactKind.PLACEHOLDER, _placeholder_kind(match), match.group(), "PH", None
    for match in _LABELLED_FIELD.finditer(text):
        value = match["value"]
        if any(field.start() < match.end() and match.start() < field.end() for field in bracketed):
            continue  # found above
        if _CASE_NUMBER.match(value):
            continue  # "avtalsnummer 23.3-1688-2024:[XXX]": the PROC rule decides
        if value.strip():
            if not is_placeholder(value):
                continue
        elif match["colon"] is None or text[match.end() : match.end() + 1] == ",":
            # An empty value counts only after a colon, at the end of a line or before
            # an empty cell: without one, the label is mostly a word in a sentence
            # ("ange sitt organisationsnummer.").
            continue
        yield FactKind.PLACEHOLDER, _placeholder_kind(match), match.group().strip(), "PH", None


def _placeholder_kind(match: re.Match[str]) -> str:
    return _PLACEHOLDER_KINDS[re.sub(r"[\s.]", "", match["label"].lower())]
