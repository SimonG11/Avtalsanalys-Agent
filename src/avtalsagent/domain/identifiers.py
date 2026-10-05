"""Normalisation of the identifiers used in the framework-agreement register.

What:
    Pure functions that turn the raw identifiers from the Excel master list
    "Alla giltiga ramavtal" into one canonical form: organisation numbers,
    agreement numbers, supplier names and sub-area paths.

Why:
    The same supplier or agreement is written in different ways in the Excel
    list and in the PDFs (e.g. `5563372381      ` vs `556337-2381`). Matching
    only works if both sides are normalised the same way. Supplier names are
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

# Agreement number, e.g. 23.3-14537-2023-001:
#   procurement case number (diarienummer) 23.3-14537-2023 + supplier sequence 001.
_AGREEMENT_NUMBER = re.compile(r"^(?P<procurement>\d+\.\d+-\d+-\d{4})-(?P<sequence>\d+)$")

# Former name in a supplier name, e.g. "AB HOLMRIS B8 (f.d. Addentity Interiör AB)".
_FORMER_NAME = re.compile(r"^(?P<name>.*?)\s*\(f\.d\.\s*(?P<former>[^)]+)\)$")

_SUB_AREA_SEPARATOR = " / "


def normalize_org_number(raw: str) -> str:
    """Return the organisation number as NNNNNN-NNNN.

    Accepts the Excel form (digits with trailing spaces) and the PDF form
    (with a hyphen). Anything that is not exactly 10 digits is rejected.
    """
    digits = re.sub(r"[\s-]", "", raw)
    if not digits.isdigit() or len(digits) != _ORG_NUMBER_DIGITS:
        raise IdentifierError(f"organisation number must have 10 digits: {raw!r}")
    return f"{digits[:6]}-{digits[6:]}"


@dataclass(frozen=True)
class AgreementNumber:
    """An agreement number split into its two parts."""

    procurement_number: str  # diarienummer, shared by all suppliers in one procurement
    sequence: str  # the supplier's sequence number within the procurement

    @property
    def full(self) -> str:
        return f"{self.procurement_number}-{self.sequence}"


def parse_agreement_number(raw: str) -> AgreementNumber:
    """Split e.g. `23.3-14537-2023-001` into `23.3-14537-2023` and `001`."""
    match = _AGREEMENT_NUMBER.match(raw.strip())
    if match is None:
        raise IdentifierError(f"agreement number has an unknown format: {raw!r}")
    return AgreementNumber(match["procurement"], match["sequence"])


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
