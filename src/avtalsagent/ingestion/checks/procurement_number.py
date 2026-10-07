"""Step-5 check: the document's own case number is a procurement of its pages.

What:
    `run` compares the case numbers a file states with the procurements of
    the agreement pages that link to it and gives one finding per number that
    is not one of them. `number_status` sums up the file's own numbers for
    the ingestion report ("Stämmer med registret"). `pages_text` and `places` word
    a file's pages and the places of a value for a finding's message; the
    other identity checks use them too.

Why:
    A document that gives itself the case number of another procurement was
    copied from, or published on, the wrong agreement, and may hold that
    agreement's content: 185c8246e536 "Prisbilaga - sammanställning Delområde
    3", linked from 23.3-8321-2024 (IT-säkerhet), is headed "23.3-1688-2024
    IT-konsulttjänster - IT-säkerhet" on p1. Such a document is held back
    until a person has looked at it (ADR 0009 decision 6). Citing another
    procurement is no deviation, only worth knowing (the same decision):
    54211e718d8e p2: "ramavtal IT-konsulttjänster Resurskonsulter, region
    Södra (dnr 23.3-7067-17) som omfattar länen".

How:
    A file's numbers are its PROCUREMENT_NUMBER facts and the procurement part
    of its AGREEMENT_NUMBER facts ("23.3.2940-20:026" is a number of
    23.3-2940-20), compared by key with `CheckContext.page_procurements`, so
    "23.3.2940-20" is the page's 23.3-2940-20 and "23.3-2649-22" its
    23.3-2649-2022. The role (SELF or CITATION) is step 4's
    (`extract/identifiers.role_of`: a number in parentheses is a citation).
    - A number of case management is never compared: the 23.5 series and the
      old letterhead form "96-15-2015", unless the register has it as a
      procurement (23.5-3718-2024, the Microsoft volume agreement).
      4b6c2a533fae p2 [page_header]: "Sid 2 (27) Dnr 23.5-1688-2024" is the
      case of the agreement-management unit, not procurement 23.3-1688-2024.
      13 pilot files state only such numbers.
    - An own number (SELF) that is not a procurement of the file's pages:
      QUARANTINE, also when the file states one that is. 18309f4961d3 (page
      23.3-10639-2023) p47: "avseende IT-drift 2023 med diarienummer
      23.3-5890-2023 och är tillämpliga på Kontraktet", while its other hits
      say 23.3-10639-2023: one of them is wrong, and the text cannot say which.
      3 pilot files: 185c8246e536, adcd1c5ed90e, 18309f4961d3.
    - A cited number (CITATION) that is not: NOTE. 1 pilot file, 54211e718d8e.
    One finding per number, however many places it stands in; its subject is
    the number in the register's spelling, or its key when the register does
    not have it.
    `number_status` looks at the file's own numbers only, as the report's
    table says ("Varje fils egna diarie- och avtalsnummer"): a citation is no
    deviation, and 54211e718d8e, whose own 23.3-2940-20 is its page's, matches.
    Of the 207 pilot files, 144 match their pages, 47 state no number, 13
    only numbers of case management, and 3 deviate (the files above).
"""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from avtalsagent.domain.extracted import Fact, FactKind, FactRole, Finding, Severity
from avtalsagent.domain.identifiers import (
    ProcurementNumber,
    parse_agreement_reference,
    parse_procurement_number,
    procurement_key,
)
from avtalsagent.ingestion.checks.context import CheckContext, CheckedFile

CHECK = "procurement_number"

_NUMBER_KINDS = (FactKind.PROCUREMENT_NUMBER, FactKind.AGREEMENT_NUMBER)


class NumberStatus(StrEnum):
    """How a file's numbers compare with its pages ("Stämmer med registret" in the report)."""

    # It has an own number to compare, and every own one is a procurement of its pages.
    # It may cite another procurement: that is a NOTE finding, not a deviation.
    MATCHES = "matches"
    NO_NUMBER = "no_number"  # it states no case or agreement number of its own
    # Its own numbers are only numbers of case management (23.5, 96-).
    CASE_MANAGEMENT_ONLY = "case_management_only"
    # An own number that is not a procurement of its pages: exactly the files `run`
    # holds back (QUARANTINE).
    DEVIATES = "deviates"


@dataclass(frozen=True)
class _Comparison:
    """A file's numbers next to the procurements of its pages, by procurement key."""

    stated: bool  # any case or agreement number of its own
    compared: bool  # ...that is not a number of case management
    own_on_pages: tuple[str, ...]  # keys of own numbers that are a procurement of its pages
    own_off_pages: dict[str, list[Fact]]  # key -> the facts that state it
    cited_off_pages: dict[str, list[Fact]]  # the same for citations, own numbers left out


def run(context: CheckContext) -> list[Finding]:
    """One finding per number of a file that is not a procurement of the file's pages."""
    findings: list[Finding] = []
    for file in context.files:
        comparison = _compare(file, context)
        for key, facts in comparison.own_off_pages.items():
            findings.append(_own_finding(file, key, facts, comparison, context))
        for key, facts in comparison.cited_off_pages.items():
            findings.append(_cited_finding(file, key, facts, context))
    return findings


def number_status(file: CheckedFile, context: CheckContext) -> NumberStatus:
    """The file's line under "Stämmer med registret", by its own numbers (see `NumberStatus`)."""
    comparison = _compare(file, context)
    if not comparison.stated:
        return NumberStatus.NO_NUMBER
    if not comparison.compared:
        return NumberStatus.CASE_MANAGEMENT_ONLY
    if comparison.own_off_pages:
        return NumberStatus.DEVIATES
    return NumberStatus.MATCHES


def pages_text(file: CheckedFile) -> str:
    """The procurements of the file's pages, for a message.

    "upphandlingen på sidan som länkar till dokumentet (23.3-8321-2024)", or
    "upphandlingarna på sidorna som länkar till dokumentet (23.3-5890-2023,
    23.3-10639-2023)". Each number is written as the first page writes it.
    """
    numbers: dict[str, str] = {}
    for link in file.links:
        for number in link.page_procurement_numbers:
            numbers.setdefault(procurement_key(number) or number, number)
    procurements = "upphandlingarna" if len(numbers) > 1 else "upphandlingen"
    pages = "sidorna" if len(file.page_urls) > 1 else "sidan"
    listed = ", ".join(numbers.values()) or "inget ramavtalsnummer"
    return f"{procurements} på {pages} som länkar till dokumentet ({listed})"


def places(facts: Sequence[Fact]) -> str:
    """Where a value stands, for a message: " (s. 7)", " (19 gånger, först på s. 2)".

    Empty for a single place without a page (a Word file). The facts are in
    block order, so the first page is where it stands first.
    """
    pages = [fact.page for fact in facts if fact.page is not None]
    if len(facts) == 1:
        return f" (s. {pages[0]})" if pages else ""
    first = f", först på s. {pages[0]}" if pages else ""
    return f" ({len(facts)} gånger{first})"


def _compare(file: CheckedFile, context: CheckContext) -> _Comparison:
    numbers = [fact for fact in file.facts if fact.kind in _NUMBER_KINDS]
    page_keys = context.page_procurements(file)
    own: defaultdict[str, list[Fact]] = defaultdict(list)
    cited: defaultdict[str, list[Fact]] = defaultdict(list)
    for fact in numbers:
        number = _procurement(fact)
        if number.is_case_management and not context.procurement_entries(number.key):
            continue
        # Step 4 gives every case and agreement number a role; a number without one
        # is taken as the file's own, the stricter reading.
        (cited if fact.role is FactRole.CITATION else own)[number.key].append(fact)
    own_off = {key: facts for key, facts in own.items() if key not in page_keys}
    return _Comparison(
        stated=any(fact.role is not FactRole.CITATION for fact in numbers),
        compared=bool(own),
        own_on_pages=tuple(key for key in own if key in page_keys),
        own_off_pages=own_off,
        cited_off_pages={
            key: facts
            for key, facts in cited.items()
            if key not in page_keys and key not in own_off
        },
    )


def _procurement(fact: Fact) -> ProcurementNumber:
    # A number fact's value is its key or the register's spelling (step 4); both parse.
    if fact.kind is FactKind.AGREEMENT_NUMBER:
        return parse_agreement_reference(fact.value).procurement
    return parse_procurement_number(fact.value)


def _own_finding(
    file: CheckedFile,
    key: str,
    facts: list[Fact],
    comparison: _Comparison,
    context: CheckContext,
) -> Finding:
    number = _spelling(key, context)
    if comparison.own_on_pages:
        others = ", ".join(_spelling(other, context) for other in comparison.own_on_pages)
        also = f"men dokumentet anger också {others}, som gör det"
    else:
        also = "och dokumentet anger inget eget nummer som gör det"
    return Finding(
        check=CHECK,
        severity=Severity.QUARANTINE,
        subject=number,
        message=(
            f"Diarienumret {number}{places(facts)} tillhör inte {pages_text(file)}, {also}. "
            f"{_register_text(key, context)}"
        ),
        sha256=file.sha256,
        evidence=facts[0].raw,
    )


def _cited_finding(
    file: CheckedFile, key: str, facts: list[Fact], context: CheckContext
) -> Finding:
    number = _spelling(key, context)
    return Finding(
        check=CHECK,
        severity=Severity.NOTE,
        subject=number,
        message=(
            f"Dokumentet hänvisar inom parentes till diarienummer {number}{places(facts)}, "
            f"som inte tillhör {pages_text(file)}. {_register_text(key, context)}"
        ),
        sha256=file.sha256,
        evidence=facts[0].raw,
    )


def _spelling(key: str, context: CheckContext) -> str:
    """A procurement number as the register writes it, or its key when the register lacks it."""
    entries = context.procurement_entries(key)
    return entries[0].procurement_number if entries else key


def _register_text(key: str, context: CheckContext) -> str:
    areas = sorted({entry.framework_area for entry in context.procurement_entries(key)})
    if not areas:
        return "Numret finns inte i registret."
    return f"I registret är det en annan upphandling inom {' och '.join(areas)}."
