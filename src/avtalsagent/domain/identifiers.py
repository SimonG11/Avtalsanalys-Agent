"""Normalisation of the identifiers used in the register and in the documents.

What:
    Pure functions that turn raw identifiers into one canonical form:
    organisation numbers, agreement numbers, supplier names and sub-area paths
    from the Excel master list "Alla giltiga ramavtal", and the case numbers
    (diarienummer), agreement numbers and organisation numbers written in the
    documents. `procurement_key` and `agreement_key` give one key for every
    spelling of a number, so a document and the register can be compared.
    `find_org_numbers` finds organisation numbers in free text.

Why:
    The same supplier or agreement is written in different ways in the Excel
    list and in the PDFs (e.g. `5563372381      ` vs `556337-2381`). Matching
    only works if both sides are normalised the same way. The real list also
    has foreign suppliers (Finnish, Danish, Norwegian, British registration
    numbers) and older agreement-number formats, so those are accepted too
    rather than dropped. Supplier names are
    never used on their own for matching, because several names can share one
    organisation number and names change over time ("f.d. ...").
    The documents write the case number in more ways than the list: with dots
    ("23.3.2940-20"), a two-digit year where the list has four
    ("23.3-8027-21" for 23.3-8027-2021) and the reverse, and the agreement
    sequence after a hyphen where the list has a colon ("23.3.2940-20:026",
    "23.3.2649-22-003"). Comparing strings would call these deviations, so
    the ingestion compares keys (M4 survey, identifiers.md §1-§2).

How:
    Each function takes a raw string and returns a normalised value, or raises
    `IdentifierError` with a message that says what was wrong. No database,
    network or LLM is involved, so every rule has its own unit test. A key is
    the series, the serial without leading zeros and the four-digit year
    ("23.3-2940-2020"), plus the agreement sequence when there is one
    ("23.3-2940-2020-018"). The key of a key is the key itself.
"""

import re
from dataclasses import dataclass


class IdentifierError(ValueError):
    """Raised when a raw identifier cannot be normalised."""


# A Swedish organisation number has 10 digits: NNNNNN-NNNN.
_ORG_NUMBER_DIGITS = 10
_SWEDISH_ORG_NUMBER = re.compile(r"^\d{6}-\d{4}$")
# A Swedish VAT number is "SE", the organisation number and "01": SE556677889901.
# None occurs in the list or in the pilot documents (M4 survey, identifiers.md §0),
# but one must give the organisation number, not a foreign registration number.
_VAT_NUMBER = re.compile(r"^SE(?P<digits>\d{10})01$")

# A foreign registration number, as written in the list: optional country or
# register prefix, then digits. Real examples: FI01148912 (Finland),
# CVR:37120928 and 33948786 (Denmark), 965920358 (Norway), FC16134 (UK).
# With a prefix five digits are enough; without one, six.
_FOREIGN_ORG_NUMBER = re.compile(r"^(?:[A-Z]{2,3}:?\d{5,12}|\d{6,12})$")

# Agreement number formats seen in the list (2026-10-05). Each has a
# procurement case number (diarienummer) and, usually, a supplier sequence.
_AGREEMENT_NUMBER_FORMATS = (
    # 23.3-14537-2023-001, and 23.3-4613-2023-003-A (a variant of agreement 003)
    re.compile(r"^(?P<procurement>\d+\.\d+-\d+-\d{4})-(?P<sequence>\d+(?:-[A-Z])?)$"),
    # Older form with a two-digit year and a colon: 23.3-2965-20:001
    re.compile(r"^(?P<procurement>\d+\.\d+-\d+-\d{2}):(?P<sequence>\d+)$"),
    # Only a case number, for agreements with one supplier: 23.5-3718-2024
    re.compile(r"^(?P<procurement>\d+\.\d+-\d+-\d{4})$"),
    # Old case number: 6765/05
    re.compile(r"^(?P<procurement>\d+/\d{2})$"),
)

# Former name in a supplier name, e.g. "AB HOLMRIS B8 (f.d. Addentity Interiör AB)".
_FORMER_NAME = re.compile(r"^(?P<name>.*?)\s*\(f\.d\.\s*(?P<former>[^)]+)\)$")

_SUB_AREA_SEPARATOR = " / "

# Kammarkollegiet's case number: series, serial and year, "23.3-5890-2023". The series
# is a code of its filing plan: 23.3 for a procurement, 23.5 for agreement management
# (the only two in the pilot, 3,092 and 165 hits). The documents also separate the
# parts with dots or en dashes ("23.3.2940-20", "23.3-8027.21"), write the serial with
# a leading zero ("23.5-03893-2021") and the year with two digits. Both separators
# being dots is refused: that is a section number ("21.3.12.20"), not a case number. A
# year must be there and have two or four digits, so "23.3.10150" (20c753d88340 §94)
# and the typo "23.3-5890-20263-XXX" (5d6e948959cc §5.2.1) are refused, not guessed.
CASE_NUMBER_PATTERN = (
    r"(?P<series>2\d\.\d)(?P<sep1>[-–.])(?P<serial>\d{2,7})(?P<sep2>[-–.])"
    r"(?P<year>(?:19|20)\d{2}|\d{2})(?!\d)"
)
# An agreement before the 23.x series, as the list writes it: "6765/05" (IBM).
OLD_NUMBER_PATTERN = r"(?P<old_serial>\d{3,5})/(?P<old_year>\d{2})"
# Kammarkollegiet's older letterhead form, "DIARIENR ... 96-15-2015" (fb9447f0b8bf).
LETTERHEAD_NUMBER_PATTERN = r"(?P<lh_series>9\d)-(?P<lh_serial>\d{1,6})-(?P<lh_year>(?:19|20)\d{2})"
# The supplier's sequence after the case number: "-001", ":018", "-003-A"; the list
# also has the two-digit "-01" (23.3-12000-2020-01).
SEQUENCE_PATTERN = r"(?P<sequence>\d{2,3}(?:-[A-Z])?)"

_PROCUREMENT_NUMBER = re.compile(
    rf"{CASE_NUMBER_PATTERN}|{OLD_NUMBER_PATTERN}|{LETTERHEAD_NUMBER_PATTERN}"
)
_AGREEMENT_REFERENCE = re.compile(
    rf"(?P<procurement>{CASE_NUMBER_PATTERN}|{OLD_NUMBER_PATTERN}|{LETTERHEAD_NUMBER_PATTERN})"
    rf"(?:[-:]{SEQUENCE_PATTERN})?"
)

# Agreement management ("Enheten för Ramavtalsförvaltning"): guides, tips and follow-up
# documents carry it in their headers. Only 23.5-3718-2024 (the Microsoft volume
# agreement) is in the list (identifiers.md §1).
CASE_MANAGEMENT_SERIES = "23.5"
OLD_SERIES = "old"  # the series of "6765/05", which has none of its own

# A Swedish organisation number in free text, with or without the hyphen
# ("202100-0829", "2021000829"). Not after a digit, hyphen or "+": phone numbers
# such as "+4687000800" (087f9c5a2f56 §pos0) have the same shape.
_ORG_NUMBER_IN_TEXT = re.compile(
    r"(?<![\d\-+])(?P<a>\d{6})\s?[-–]\s?(?P<b>\d{4})(?![\d\-])"
    r"|(?<![\d\-+])(?P<c>\d{6})(?P<d>\d{4})(?![\d\-])"
)
# A Swedish VAT number in free text: "SE556677889901", "SE 556677-8899 01". Strict on
# purpose: "SE" with any digits gives 105 NUTS codes in the pilot ("SE121 Uppsala").
_VAT_NUMBER_IN_TEXT = re.compile(r"\bSE\s?(?P<a>\d{6})-?(?P<b>\d{4})\s?01\b")

# An unfilled template field. Real examples: "XXXXXX-XXXX", "[xxxxxx-yyyy]",
# "avtalsnummer XX", "23.3-8321-2024-XXX" (two or more x as a word); "[X]",
# "[organisationsnummer]", "[Ramavtalsleverantören]" (a bracketed field); and the
# prompt of a Word content control, "Klicka här för att ange text".
_PLACEHOLDER = re.compile(
    r"\b[Xx]{2,}\b|\[[^\]]*[^\W\d_][^\]]*\]|Klicka här för att ange", re.IGNORECASE
)
# What is left of an empty field: "Organisationsnummer: |" in a table.
_EMPTY_FIELD = " \t\n|:.,;"


def normalize_org_number(raw: str) -> str:
    """Return a Swedish organisation number as NNNNNN-NNNN, or a foreign one as written.

    Swedish: the Excel form (digits with trailing spaces), the PDF form (with a
    hyphen) and the VAT form (SE556677889901) give NNNNNN-NNNN. Foreign:
    spaces are removed and letters upper-cased, nothing else, because each
    country has its own format. Anything else is rejected.
    """
    digits = re.sub(r"[\s-]", "", raw)
    if digits.isdigit() and len(digits) == _ORG_NUMBER_DIGITS:
        return f"{digits[:6]}-{digits[6:]}"
    vat = _VAT_NUMBER.match(digits.upper())
    if vat is not None:
        return f"{vat['digits'][:6]}-{vat['digits'][6:]}"
    compact = re.sub(r"\s", "", raw).upper()
    if _FOREIGN_ORG_NUMBER.match(compact):
        return compact
    raise IdentifierError(f"organisation number has an unknown format: {raw!r}")


def is_swedish_org_number(org_number: str) -> bool:
    """True for a normalised Swedish organisation number (NNNNNN-NNNN)."""
    return _SWEDISH_ORG_NUMBER.match(org_number) is not None


def luhn_valid(org_number: str) -> bool:
    """True when a Swedish organisation number's last digit is its check digit (mod 10).

    Accepts NNNNNN-NNNN or ten digits. Every organisation number in the pilot
    documents passes (254 of 254), and every phone number of the same shape fails.
    """
    digits = org_number.replace("-", "")
    if len(digits) != _ORG_NUMBER_DIGITS or not digits.isdigit():
        return False
    total = 0
    for position, digit in enumerate(digits):
        value = int(digit) * (2 if position % 2 == 0 else 1)
        total += value - 9 if value > 9 else value
    return total % 10 == 0


def find_org_numbers(text: str) -> list[tuple[int, int, str]]:
    """Find Swedish organisation numbers in free text: (start, end, "NNNNNN-NNNN").

    A number counts only if its check digit is right (`luhn_valid`), which drops
    phone numbers of the same shape. VAT numbers give the organisation number in
    them. Foreign registration numbers are not looked for: in free text their
    forms are those of CPV codes and dates ("48490000", "20221125").
    """
    found: list[tuple[int, int, str]] = []
    # VAT numbers first: "SE 556677-8899 01" also holds the plain form.
    for pattern in (_VAT_NUMBER_IN_TEXT, _ORG_NUMBER_IN_TEXT):
        for match in pattern.finditer(text):
            digits = "".join(part for part in match.groups() if part)
            inside = any(start <= match.start() < end for start, end, _ in found)
            if luhn_valid(digits) and not inside:
                found.append((match.start(), match.end(), f"{digits[:6]}-{digits[6:]}"))
    return sorted(found)


def is_placeholder(raw: str) -> bool:
    """True when the value of a field is an unfilled template field.

    `raw` is the text where the value should be, e.g. what follows
    "organisationsnummer". An empty value counts too ("Organisationsnummer: |"),
    so the caller decides where a value was expected.
    """
    text = raw.strip(_EMPTY_FIELD)
    return not text or _PLACEHOLDER.search(text) is not None


@dataclass(frozen=True)
class ProcurementNumber:
    """A case number (diarienummer) split into its parts."""

    series: str  # "23.3", "23.5", OLD_SERIES for "6765/05", "96" for "96-15-2015"
    serial: int
    year: int  # four digits; a two-digit year is 20YY
    raw: str  # as written

    @property
    def key(self) -> str:
        """The same for every spelling: "23.3-2940-2020", "6765/05", "96-15-2015"."""
        if self.series == OLD_SERIES:
            return f"{self.serial}/{self.year % 100:02d}"
        return f"{self.series}-{self.serial}-{self.year}"

    @property
    def is_case_management(self) -> bool:
        """A case of Kammarkollegiet's own administration, not a procurement.

        The 23.5 series is agreement management; the letterhead form "96-15-2015"
        is the authority's own case for a letter. Such a number is stored but
        never compared with the procurements of a page.
        """
        # The letterhead series is two digits starting with 9 (LETTERHEAD_NUMBER_PATTERN).
        return self.series == CASE_MANAGEMENT_SERIES or self.series.startswith("9")


@dataclass(frozen=True)
class AgreementReference:
    """An agreement number as written in a document: a case number and a sequence."""

    procurement: ProcurementNumber
    sequence: str | None  # "018"; None for an agreement with one supplier ("6765/05")
    raw: str

    @property
    def key(self) -> str:
        """The procurement key plus the sequence: "23.3-2940-2020-018".

        An agreement without a sequence has its procurement's key.
        """
        if self.sequence is None:
            return self.procurement.key
        return f"{self.procurement.key}-{self.sequence}"


def parse_procurement_number(raw: str) -> ProcurementNumber:
    """Read a case number in any spelling the register or the documents use.

    "23.3-2940-20", "23.3.2940-20", "23.3-8027.21", "23.5-03893-2021",
    "6765/05" and "96-15-2015" are accepted; see `CASE_NUMBER_PATTERN` for what
    is refused and why.
    """
    text = raw.strip()
    match = _PROCUREMENT_NUMBER.fullmatch(text)
    if match is None:
        raise IdentifierError(f"procurement number has an unknown format: {raw!r}")
    return _procurement_number(match, text)


def parse_agreement_reference(raw: str) -> AgreementReference:
    """Read an agreement number in any spelling: "23.3.2940-20:026", "23.3.2649-22-003"."""
    text = raw.strip()
    match = _AGREEMENT_REFERENCE.fullmatch(text)
    if match is None:
        raise IdentifierError(f"agreement number has an unknown format: {raw!r}")
    return AgreementReference(
        _procurement_number(match, match["procurement"]), match["sequence"], text
    )


def procurement_key(raw: str) -> str | None:
    """The key of a case number, or None when it cannot be read."""
    try:
        return parse_procurement_number(raw).key
    except IdentifierError:
        return None


def agreement_key(raw: str) -> str | None:
    """The key of an agreement number, or None when it cannot be read.

    An agreement number without a sequence ("23.5-3718-2024", "6765/05") gets
    its procurement's key.
    """
    try:
        return parse_agreement_reference(raw).key
    except IdentifierError:
        return None


def _procurement_number(match: re.Match[str], raw: str) -> ProcurementNumber:
    if match["series"] is not None:
        if match["sep1"] == match["sep2"] == ".":
            raise IdentifierError(f"a section number, not a procurement number: {raw!r}")
        return ProcurementNumber(
            match["series"], int(match["serial"]), _four_digit_year(match["year"]), raw
        )
    if match["old_serial"] is not None:
        return ProcurementNumber(
            OLD_SERIES, int(match["old_serial"]), _four_digit_year(match["old_year"]), raw
        )
    return ProcurementNumber(
        match["lh_series"], int(match["lh_serial"]), int(match["lh_year"]), raw
    )


def _four_digit_year(year: str) -> int:
    # Every two-digit year in the list and the pilot documents is 2005-2026.
    return int(year) + 2000 if len(year) == 2 else int(year)


@dataclass(frozen=True)
class AgreementNumber:
    """An agreement number split into its parts."""

    full: str  # as written in the list, e.g. 23.3-2965-20:001
    procurement_number: str  # diarienummer, shared by all suppliers in one procurement
    sequence: str | None  # the supplier's sequence number; None if the number has none


def parse_agreement_number(raw: str) -> AgreementNumber:
    """Split e.g. `23.3-14537-2023-001` into `23.3-14537-2023` and `001`.

    See `_AGREEMENT_NUMBER_FORMATS` for the accepted formats.
    """
    text = raw.strip()
    for pattern in _AGREEMENT_NUMBER_FORMATS:
        match = pattern.match(text)
        if match is not None:
            return AgreementNumber(text, match["procurement"], match.groupdict().get("sequence"))
    raise IdentifierError(f"agreement number has an unknown format: {raw!r}")


@dataclass(frozen=True)
class SupplierName:
    """A supplier name with the former name ("f.d.") split out, if any."""

    name: str
    former_name: str | None


def parse_supplier_name(raw: str) -> SupplierName:
    """Split `AB HOLMRIS B8 (f.d. Addentity Interiör AB)` into name and former name."""
    text = " ".join(raw.split())
    if not text:
        raise IdentifierError("supplier name is empty")
    match = _FORMER_NAME.match(text)
    if match is None:
        return SupplierName(text, None)
    return SupplierName(match["name"], match["former"].strip())


def split_sub_area(raw: str) -> tuple[str, ...]:
    """Split a sub-area path on " / " into its levels, from broadest to narrowest.

    Example: `Gävleborgs län / Gävle / Gävle zon 1 - Longstay`
    -> (`Gävleborgs län`, `Gävle`, `Gävle zon 1 - Longstay`).
    """
    levels = tuple(part.strip() for part in raw.split(_SUB_AREA_SEPARATOR))
    if not all(levels):
        raise IdentifierError(f"sub-area has an empty level: {raw!r}")
    return levels
