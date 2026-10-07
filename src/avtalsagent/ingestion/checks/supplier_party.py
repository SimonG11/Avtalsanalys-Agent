"""Step-5 check: a supplier card names the register's supplier as its party.

What:
    `run` compares the supplier a supplier card's party clause names with the
    supplier the register has for the card's agreement: QUARANTINE when the
    organisation number differs or the supplier's slot is not filled in, NOTE
    when the number is the same but the name is written differently.
    `supplier_parties` gives the facts it compares, so that `org_numbers` can
    leave them to this check.

Why:
    The party clause says which legal entity signed the agreement, and a card
    can name another one than the register: 7a49e1a61b31 p7: "och ÅF Digital
    Solutions AB, organisationsnummer 556866-4444 nedan
    Ramavtalsleverantören", where the register has AFRY Sweden AB 556224-8012
    for 23.3-2940-20:033. Whether that is a legitimate succession is outside
    the text, so the card is held back until a person has accepted it
    (ADR 0009 decision 6). The organisation number decides, never the name:
    names change while the number stays.
    b0f5951c99b2 p7: "och Knowit & Precio Fishbone Public IT AB,
    organisationsnummer 559309-6794" is the register's Knowit Public IT AB,
    so that is only worth knowing.

How:
    The facts are the PARTY facts of the supplier's slot (rule E1 of
    `extract/parties.py`) in a file one of whose links carries an agreement
    number; the agreement's register entries come by key
    (`CheckContext.agreement_entries`).
    - Their organisation number is not the register's for the agreement, or
      the register does not have the agreement: QUARANTINE, one finding per
      organisation number.
    - Same number, but the name differs from the register's name and former
      name after `normalise_name`: NOTE, one finding per name.
    - The supplier's slot holds no number (a PLACEHOLDER fact of rule E1,
      "organisationsnummer [xxxxxx-yyyy]"): QUARANTINE, one finding per
      agreement, with the agreement number as its subject. A signed card has
      the slot filled, so the file may be the agreement's template, and the
      party cannot be compared with the register.
    In the pilot's 25 cards: 4 QUARANTINE (ÅF Digital Solutions AB in
    7a49e1a61b31 and ee6107229c37, Tieto Sweden AB in a09791e460a4 and
    f5823eb88227) and 1 NOTE (b0f5951c99b2); one more name differs only in
    case (77d641b81cc2). Every card has its slot filled.
    A card without a party clause gives no finding here; its organisation
    numbers are then all checked by `org_numbers` (none in the pilot: the
    clause was read in all 25 cards).
"""

import re
from collections import defaultdict
from collections.abc import Sequence

from avtalsagent.domain.extracted import Fact, FactKind, Finding, Severity
from avtalsagent.domain.register import RegisterEntry
from avtalsagent.ingestion.checks.context import CheckContext, CheckedFile
from avtalsagent.ingestion.extract.parties import SUPPLIER_SLOT_RULE

CHECK = "supplier_party"


# What a name may differ in and still be the register's: case, punctuation and the
# Swedish company form. 77d641b81cc2 p3: "Chas visual management AB" is the
# register's "Chas Visual Management AB", and three pilot files write "Knowit
# Aktiebolag (publ)" for "Knowit Aktiebolag". Foreign forms (Ltd, Corporation, Oy)
# stay part of the name: "Microsoft Ireland Operations Ltd" is not Microsoft AB.
_COMPANY_FORM_WORDS = frozenset({"ab", "aktiebolag", "aktiebolaget", "publ"})
_NOT_LETTER_OR_DIGIT = re.compile(r"[\W_]+")


def supplier_parties(file: CheckedFile) -> list[Fact]:
    """The PARTY facts of the supplier's slot of a supplier card; none for another file."""
    if not any(link.agreement_number for link in file.links):
        return []
    return [
        fact
        for fact in file.facts
        if fact.kind is FactKind.PARTY and fact.rule == SUPPLIER_SLOT_RULE
    ]


def normalise_name(name: str) -> str:
    """A company name without case, punctuation and the Swedish company form.

    "Knowit Aktiebolag (publ)" -> "knowit", "Chas visual management AB" ->
    "chas visual management".
    """
    words = _NOT_LETTER_OR_DIGIT.sub(" ", name.casefold()).split()
    return " ".join(word for word in words if word not in _COMPANY_FORM_WORDS)


def run(context: CheckContext) -> list[Finding]:
    """Compare each supplier card's party with the register's supplier of its agreement."""
    findings: list[Finding] = []
    for file in context.files:
        agreements = list(
            dict.fromkeys(link.agreement_number for link in file.links if link.agreement_number)
        )
        by_org: defaultdict[str, list[Fact]] = defaultdict(list)
        for fact in supplier_parties(file):
            by_org[fact.value].append(fact)
        for org_number, facts in by_org.items():
            findings.extend(_compare(file, org_number, facts, agreements, context))
        # Only a card with no filled supplier slot: a filled clause above a template's blank
        # one (none in the pilot) is the card's own and is compared above.
        unfilled = _unfilled_slots(file)
        if unfilled and not by_org:
            findings.extend(
                _unfilled_finding(file, unfilled[0], agreement, context) for agreement in agreements
            )
    return findings


def _unfilled_slots(file: CheckedFile) -> list[Fact]:
    """The PLACEHOLDER facts of the supplier's slot of a supplier card; none for another file."""
    if not any(link.agreement_number for link in file.links):
        return []
    return [
        fact
        for fact in file.facts
        if fact.kind is FactKind.PLACEHOLDER and fact.rule == SUPPLIER_SLOT_RULE
    ]


def _compare(
    file: CheckedFile,
    org_number: str,
    facts: list[Fact],
    agreements: Sequence[str],
    context: CheckContext,
) -> list[Finding]:
    entries = {agreement: context.agreement_entries(agreement) for agreement in agreements}
    # A file in two cards (none in the pilot) must be the supplier's of both.
    for agreement, register in entries.items():
        if org_number not in {entry.org_number for entry in register}:
            return [_org_finding(file, org_number, facts[0], agreement, register, context)]
    names = {
        normalise_name(name)
        for register in entries.values()
        for entry in register
        for name in (entry.supplier_name, entry.former_supplier_name)
        if name
    }
    written = dict.fromkeys(fact.name for fact in facts if fact.name)
    return [
        _name_finding(file, name, facts, agreements[0], entries[agreements[0]])
        for name in written
        if normalise_name(name) not in names
    ]


def _org_finding(
    file: CheckedFile,
    org_number: str,
    fact: Fact,
    agreement: str,
    register: list[RegisterEntry],
    context: CheckContext,
) -> Finding:
    named = f"{fact.name}, organisationsnummer {org_number}," if fact.name else org_number
    if register:
        expected = register[0]
        message = (
            f"Leverantörskortet för avtal {agreement} har {named} som avtalspart, men "
            f"registret har {expected.supplier_name}, {expected.org_number}, för avtalet. "
            f"{_org_register_text(org_number, context)}"
        )
    else:
        message = (
            f"Leverantörskortet för avtal {agreement} har {named} som avtalspart, men "
            "avtalet finns inte i registret."
        )
    return Finding(
        check=CHECK,
        severity=Severity.QUARANTINE,
        subject=org_number,
        message=message,
        sha256=file.sha256,
        agreement_number=agreement,
        evidence=fact.raw,
    )


def _name_finding(
    file: CheckedFile,
    name: str,
    facts: list[Fact],
    agreement: str,
    register: list[RegisterEntry],
) -> Finding:
    fact = next(fact for fact in facts if fact.name == name)
    return Finding(
        check=CHECK,
        severity=Severity.NOTE,
        subject=name,
        message=(
            f"Leverantörskortet för avtal {agreement} skriver avtalsparten {name}, men "
            f"registret har namnet {register[0].supplier_name} för samma organisationsnummer "
            f"({fact.value})."
        ),
        sha256=file.sha256,
        agreement_number=agreement,
        evidence=fact.raw,
    )


def _unfilled_finding(
    file: CheckedFile, fact: Fact, agreement: str, context: CheckContext
) -> Finding:
    register = context.agreement_entries(agreement)
    expected = (
        f"registrets {register[0].supplier_name}, {register[0].org_number}"
        if register
        else "en leverantör i registret (avtalet finns inte där)"
    )
    return Finding(
        check=CHECK,
        severity=Severity.QUARANTINE,
        subject=agreement,
        message=(
            f"Partsklausulen i leverantörskortet för avtal {agreement} anger inget "
            "organisationsnummer för leverantören, så det går inte att se att avtalsparten är "
            f"{expected}. Ett undertecknat avtal har fältet ifyllt."
        ),
        sha256=file.sha256,
        agreement_number=agreement,
        evidence=fact.raw,
    )


def _org_register_text(org_number: str, context: CheckContext) -> str:
    names = sorted({e.supplier_name for e in context.register if e.org_number == org_number})
    if not names:
        return f"Organisationsnumret {org_number} finns inte i registret."
    return f"I registret är {org_number} {' och '.join(names)}."
