"""Step-5 check: a document states only its own supplier's agreement number.

What:
    `run` gives a QUARANTINE finding for each agreement number (a case number
    with a supplier's sequence, "23.3.2940-20:026") that a file states as its
    own when it should not: a supplier card's file that states another
    agreement than its card, or a file outside the supplier cards that states
    one at all.

Why:
    A supplier card holds the documents of one supplier's agreement, and its
    signed "Ramavtal" names that agreement on the cover and in the parties
    clause: 7a49e1a61b31 p1: "IT-konsulttjänster 2020 Dnr 23.3-2940-20 Ramavtal
    23.3.2940-20:033 ÅF Digital Solutions AB". Another number there means the
    file is in the wrong card. A file for the whole area must not carry one
    supplier's number: the generic main document for IT-säkerhet has supplier
    001's number in its page headers, 65d611d12eab p2 [page_header]:
    "23.3-8321-2024-001 IT-konsulttjänster - IT-säkerhet", while its text has
    the unfilled "23.3-8321-2024-XXX" (M4 survey, identifiers.md §2). Such a
    file could be taken for supplier 001's own agreement, so it is held back
    until a person has looked at it (M4 design, decision 3).

How:
    Only AGREEMENT_NUMBER facts with the role SELF count; a number in
    parentheses is a citation (`extract/identifiers.role_of`). Step 4 gives
    such a fact only for a case number followed by a supplier's sequence; an
    unfilled sequence ("23.3-8321-2024-XXX", "23.3-1688-2024:[XXX]") gives a
    PLACEHOLDER fact and the procurement number instead, so a template does
    not get here. Numbers are compared by key, so "23.3.2940-20:033" is the
    card's "23.3-2940-20:033". A file is a supplier card's when one of its
    links carries an agreement number.
    - In a supplier card's file, an own number that is not its card's:
      QUARANTINE (none in the pilot: the 25 cards state only their own).
    - In any other file, an own agreement number: QUARANTINE (65d611d12eab
      alone in the pilot).
    One finding per number; its subject is the number in the register's
    spelling, or its key when the register does not have it.
"""

from collections import defaultdict

from avtalsagent.domain.extracted import Fact, FactKind, FactRole, Finding, Severity
from avtalsagent.domain.identifiers import agreement_key
from avtalsagent.ingestion.checks.context import CheckContext, CheckedFile
from avtalsagent.ingestion.checks.procurement_number import places

CHECK = "agreement_number"


def run(context: CheckContext) -> list[Finding]:
    """One finding per own agreement number a file should not state."""
    findings: list[Finding] = []
    for file in context.files:
        cards = [link.agreement_number for link in file.links if link.agreement_number]
        card_keys = {agreement_key(number) for number in cards}
        for key, facts in _own_numbers(file).items():
            if not cards:
                findings.append(_area_finding(file, facts, context))
            elif key not in card_keys:
                findings.append(_card_finding(file, cards[0], facts, context))
    return findings


def _own_numbers(file: CheckedFile) -> dict[str, list[Fact]]:
    """The file's own agreement numbers by key, each with the facts that state it."""
    numbers: defaultdict[str, list[Fact]] = defaultdict(list)
    for fact in file.facts:
        if fact.kind is FactKind.AGREEMENT_NUMBER and fact.role is FactRole.SELF:
            numbers[agreement_key(fact.value) or fact.value].append(fact)
    return dict(numbers)


def _card_finding(
    file: CheckedFile, card: str, facts: list[Fact], context: CheckContext
) -> Finding:
    number = facts[0].value
    return Finding(
        check=CHECK,
        severity=Severity.QUARANTINE,
        subject=number,
        message=(
            f"Dokumentet ligger i leverantörskortet för avtal {card} men anger avtalsnumret "
            f"{number}{places(facts)} som sitt eget. {_register_text(number, context)}"
        ),
        sha256=file.sha256,
        agreement_number=file.metadata.agreement_number or card,
        evidence=facts[0].raw,
    )


def _area_finding(file: CheckedFile, facts: list[Fact], context: CheckContext) -> Finding:
    number = facts[0].value
    return Finding(
        check=CHECK,
        severity=Severity.QUARANTINE,
        subject=number,
        message=(
            f"Dokumentet ligger inte i något leverantörskort men anger avtalsnumret "
            f"{number}{places(facts)}, en enskild leverantörs avtal, som sitt eget. "
            f"{_register_text(number, context)}"
        ),
        sha256=file.sha256,
        agreement_number=number,
        evidence=facts[0].raw,
    )


def _register_text(number: str, context: CheckContext) -> str:
    suppliers = sorted({entry.supplier_name for entry in context.agreement_entries(number)})
    if not suppliers:
        return "Avtalsnumret finns inte i registret."
    return f"I registret är det avtalet med {' och '.join(suppliers)}."
