"""Normalisation of the identifiers used in the framework-agreement register.

What:
    Pure functions that turn the raw identifiers from the Excel master list
    "Alla giltiga ramavtal" into one canonical form: organisation numbers,
    agreement numbers, supplier names and sub-area paths.

Why:
    The same supplier or agreement is written in different ways in the Excel
    list and in the PDFs (e.g. `5563372381      ` vs `556337-2381`). Matching
    only works if both sides are normalised the same way. The real list also
    has foreign suppliers (Finnish, Danish, Norwegian, British registration
    numbers) and older agreement-number formats, so those are accepted too
    rather than dropped. Supplier names are
    never used on their own for matching, because several names can share one
    organisation number and names change over time ("f.d. ...").

How:
    Each function takes a raw string and returns a normalised value, or raises
    `IdentifierError` with a message that says what was wrong. No database,
    network or LLM is involved, so every rule has its own unit test.
"""

import re
from dataclasses import dataclass


class IdentifierError(ValueError):
    """Raised when a raw identifier cannot be normalised."""


# A Swedish organisation number has 10 digits: NNNNNN-NNNN.
_ORG_NUMBER_DIGITS = 10
_SWEDISH_ORG_NUMBER = re.compile(r"^\d{6}-\d{4}$")

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


def normalize_org_number(raw: str) -> str:
    """Return a Swedish organisation number as NNNNNN-NNNN, or a foreign one as written.

    Swedish: the Excel form (digits with trailing spaces) and the PDF form
    (with a hyphen) both give NNNNNN-NNNN. Foreign: spaces are removed and
    letters upper-cased, nothing else, because each country has its own
    format. Anything else is rejected.
    """
    digits = re.sub(r"[\s-]", "", raw)
    if digits.isdigit() and len(digits) == _ORG_NUMBER_DIGITS:
        return f"{digits[:6]}-{digits[6:]}"
    compact = re.sub(r"\s", "", raw).upper()
    if _FOREIGN_ORG_NUMBER.match(compact):
        return compact
    raise IdentifierError(f"organisation number has an unknown format: {raw!r}")


def is_swedish_org_number(org_number: str) -> bool:
    """True for a normalised Swedish organisation number (NNNNNN-NNNN)."""
    return _SWEDISH_ORG_NUMBER.match(org_number) is not None


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
