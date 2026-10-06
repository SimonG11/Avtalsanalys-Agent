"""Step-5 check: every organisation number in a document is a supplier of its pages.

What:
    `run` gives a QUARANTINE finding for each organisation number in a file
    that is not the number of a supplier on a procurement of the pages that
    link to the file. Kammarkollegiet's own number, and the supplier in a
    supplier card's party clause (`supplier_party` checks that one), are left
    out.

Why:
    The only organisation numbers in the pilot documents are Kammarkollegiet's
    and its suppliers', in party clauses and in supplier tables (M4 survey,
    identifiers.md §4; subcontractors' and customers' numbers do not occur).
    A number that is no supplier of the agreement means the document names
    another legal entity than the register, and a person must say why before
    it is indexed (M4 design, decision 3): 171a3cacf5fd p1: "och Microsoft
    Ireland Operations Ltd, organisationsnummer 502052-1307 (nedan
    Microsoft)", where the register has Microsoft AB 556233-4804 for
    23.5-3718-2024; and 8d679cb2ebef p1, the supplier table of "Prisbilaga -
    sammanställning Delområde 1": "ÅF Digital Solutions AB | ... Organisations
    nr | ... | 556866-4444", where 23.3-1688-2024-009 is AFRY Sweden AB
    556224-8012. Names are not compared: one organisation can have several
    names, and the number decides (M4 survey, suppliers.md §7).

How:
    The numbers are the ORG_NUMBER facts (a check digit that is right) and
    the PARTY facts outside a card's supplier slot, whose number step 4 keeps
    even with a wrong check digit (`extract/parties.py`), so a mistyped
    supplier number in a main document reaches this check. A number is right
    when a register entry of a procurement of one of the file's pages
    (`CheckContext.page_entries`) has it. One finding per number; the message
    says whether the register has the number at all, and for which supplier.
    In the pilot: 2 files (171a3cacf5fd, 8d679cb2ebef), both with a number
    that is in no register row.
"""

from collections import defaultdict

from avtalsagent.domain.extracted import (
    CONTRACTING_AUTHORITY_ORG_NUMBER,
    Fact,
    FactKind,
    Finding,
    Severity,
)
from avtalsagent.domain.register import RegisterEntry
from avtalsagent.ingestion.checks.context import CheckContext, CheckedFile
from avtalsagent.ingestion.checks.procurement_number import pages_text, places
from avtalsagent.ingestion.checks.supplier_party import supplier_parties

CHECK = "org_numbers"

_ORG_KINDS = (FactKind.ORG_NUMBER, FactKind.PARTY)


def run(context: CheckContext) -> list[Finding]:
    """One finding per organisation number of a file that is no supplier of its pages."""
    register: defaultdict[str, list[RegisterEntry]] = defaultdict(list)
    for entry in context.register:
        register[entry.org_number].append(entry)
    findings: list[Finding] = []
    for file in context.files:
        suppliers = {
            entry.org_number for link in file.links for entry in context.page_entries(link)
        }
        for org_number, facts in _org_numbers(file).items():
            if org_number not in suppliers:
                findings.append(_finding(file, org_number, facts, register.get(org_number, [])))
    return findings


def _org_numbers(file: CheckedFile) -> dict[str, list[Fact]]:
    """The organisation numbers to check, each with the facts that state it."""
    left_out = {CONTRACTING_AUTHORITY_ORG_NUMBER}
    left_out.update(fact.value for fact in supplier_parties(file))
    numbers: defaultdict[str, list[Fact]] = defaultdict(list)
    for fact in file.facts:
        if fact.kind in _ORG_KINDS and fact.value not in left_out:
            numbers[fact.value].append(fact)
    return dict(numbers)


def _finding(
    file: CheckedFile, org_number: str, facts: list[Fact], register: list[RegisterEntry]
) -> Finding:
    # A party clause names the company and is better evidence than the number alone.
    party = next((fact for fact in facts if fact.kind is FactKind.PARTY), None)
    stated = [fact for fact in facts if fact.kind is FactKind.ORG_NUMBER] or facts
    named = f", som dokumentet anger för {party.name}," if party is not None and party.name else ""
    if register:
        suppliers = sorted({entry.supplier_name for entry in register})
        procurements = sorted({entry.procurement_number for entry in register})
        known = (
            f"I registret är det {' och '.join(suppliers)}, leverantör på "
            f"{', '.join(procurements)}."
        )
    else:
        known = "Numret finns inte i registret."
    return Finding(
        check=CHECK,
        severity=Severity.QUARANTINE,
        subject=org_number,
        message=(
            f"Organisationsnumret {org_number}{places(stated)}{named} tillhör ingen "
            f"leverantör på {pages_text(file)}. {known}"
        ),
        sha256=file.sha256,
        evidence=(party or stated[0]).raw,
    )
