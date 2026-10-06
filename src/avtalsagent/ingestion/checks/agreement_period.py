"""Step-5 check: the agreement period a document or a page states agrees with the register.

What:
    `run` compares the agreement period each file states (the period facts of
    `ingestion/extract/dates.py`, one statement at a time) with the register's
    dates, and the period each agreement page states ("2024-11-14 -
    2028-11-13") with the register's dates for the page's sub-area. A document
    whose period deviates is held back (QUARANTINE), an agreement that started
    later than its document says gives a NOTE, and a page whose period
    deviates gives a REPORT.

Why:
    The period is part of a document's own identity (M4 design, decision 3): a
    signed agreement that states other dates than the register is another
    version or shows a register error, and a person should look before it is
    indexed. Two things make a plain comparison wrong; the rules below exist
    for them (M4 survey dates.md §2-§4):
    - Extensions. The IT-konsulttjänster 2020 cards state a first period of 24
      months and an extension of at most 24 months, which has been used: the
      register has the extended end. 14aa1cc8ee3d §9.6.2: "tidigast från och
      med den 2022-12-01. Från 2022-12-01 löper ramavtalet därefter under en
      period av 24 månader", §9.6.3: "förlängningar av Ramavtalets
      giltighetstid uppgå till maximalt 24 månader"; register 2022-12-01 -
      2026-11-30.
    - Sub-areas. A guide states one period per sub-area, and a procurement can
      have different dates in different sub-areas. Comparing with the whole
      procurement gave 4 false deviations in the survey. 4b6c2a533fae §2.4:
      "Ramavtalet för område 3 - IT-säkerhet är giltigt från och med 2026-03-10
      och till och med 2030-03-09".

How:
    A statement is the period facts of one clause or table row
    (`Fact.statement`). Its start is its PERIOD_START; its end is its
    PERIOD_END, or else start + PERIOD_MONTHS months - 1 day (2022-12-01 + 24
    months: 2024-11-30). A statement with neither ("löper under en period av
    48 månader" alone) is not compared.

    What a statement is compared with, the earliest valid_from and latest
    valid_to of some register entries:
    - In a supplier's card (a link with an agreement number): that
      agreement's entries, whatever the statement's scope.
    - In any other file: the sub-area of the pages that link to it
      (`CheckContext.page_scope`). A statement with a scope is compared only
      with the pages it names: "område 3 - IT-säkerhet" and "AO3" name the
      page whose title has "3." ("IT-konsulttjänster 3. IT-säkerhet"); a table
      row label names the page whose title is in it, or that it is in
      (49f36699a469 b83 "Programvaror och Tjänster Systemutveckling" and the
      page "Systemutveckling"). An unscoped statement goes with every page of
      the file. When those pages' sub-areas have different periods, the
      statement is skipped, since it cannot be right for all of them: the
      Bemanningstjänster documents 34d71a7e4da0 and c59dbfeeb576 are linked
      from pages whose sub-areas end 2029-04-02 and 2029-04-21. A scoped
      statement that names none of the file's pages is skipped too: no page
      gives register dates for that sub-area, and another sub-area's dates
      would make a false deviation. (Neither skip happens in the pilot: those
      two documents state no dated period, and every scoped statement names
      a page of its file.)

    Start and end are compared on their own:
    - The same dates: no finding.
    - Extension used: stated end < register end <= stated end + the extension
      (no finding). The extension is the statement's own (a cover's "Ingen
      förlängning" is 0), else the longest the file states in another
      statement (the cards above state it in the next clause), else the
      register's "Max förl. till".
    - Later start: the register's start is later than the stated one, and the
      end agrees as above: NOTE. 185872a6bb90 §1.8 allows it: "Om Parterna
      signerar Ramavtalet vid ett senare datum träder Ramavtalet i kraft från
      och med det datumet".
    - Anything else: QUARANTINE, one finding per stated period.

    Not compared: planned starts ("beräknas träda i kraft", PLANNED_START),
    which are plans and have no statement; files without period facts; and
    templates (`DocumentMetadata.is_template`). A template's dates are no
    agreement's: by M4 decision 4 a template other than the type TEMPLATE
    states no start or end, and a call-off template ("Avropsmall") that has
    them states the call-off's.

    Documents of the procurement stage, a TendSign printout with the cover
    "Upphandlingsdokument" or a document of the procurement group (tender
    invitations, questions and answers), state the period as planned before
    the award, as the planned starts do (dates.md §5: 8 of 14 differ from the
    register). A deviation in one is a NOTE, not a quarantine: the document is
    right about its own stage. The signed printout (cover "Ramavtal") is
    checked like any agreement.

    The pages: `parse_period` of the page's period against its sub-area's
    earliest valid_from and latest valid_to: the same dates, or a REPORT (one
    per page, about no file). A page without a period, or one `parse_period`
    cannot read, is not compared.

    Pilot (207 files, 17 pages; dates.md §3-§4): 0 QUARANTINE and 0 NOTE; a
    REPORT for each of the 2 Bemanningstjänster Kontorstjänster pages
    (2025-04-22 - 2029-04-21, while 7 of their 18 and 19 agreements start
    2025-04-03). Of the 37 typed main documents, 13 match (the 7 cards of
    23.3-2649-2022, 5 TendSign covers and the Microsoft footer), the 18 cards
    of 23.3-2940-20 have used their extension, and 6 state no dated period (5
    templates and the printout cbe12fd30683). The 19 statements of other files
    (5 covers, 4 guides; 11 of them scoped) match their sub-area. No template
    or procurement-stage file (51 and 57 files) states a dated period, so
    those two rules change nothing in the pilot.
"""

import calendar
import re
from collections import defaultdict
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum

from avtalsagent.domain.documents import CatalogLink
from avtalsagent.domain.extracted import DocumentGroup, Fact, FactKind, Finding, Severity
from avtalsagent.domain.identifiers import agreement_key
from avtalsagent.domain.register import RegisterEntry
from avtalsagent.ingestion.checks.context import CheckContext, CheckedFile
from avtalsagent.ingestion.extract.dates import parse_period

CHECK = "agreement_period"

_PERIOD_KINDS = (
    FactKind.PERIOD_START,
    FactKind.PERIOD_END,
    FactKind.PERIOD_MONTHS,
    FactKind.EXTENSION_MONTHS,
)
# A sub-area named by its number: "område 3 - IT-säkerhet" (4b6c2a533fae §2.4) or the
# area code "AO3" (4b6c2a533fae b16 "AO3 träder i kraft per 2026-03-10"). The pages
# write the number with a full stop: "IT-konsulttjänster 3. IT-säkerhet".
_AREA_NUMBER = re.compile(r"^(?:område\s+|AO)(?P<number>\d+)\b", re.IGNORECASE)
# The cover word of a TendSign printout from the procurement stage (`tendsign_cover`).
_PROCUREMENT_COVER = "Upphandlingsdokument"
# A finding that quarantines wins over one that reports or notes the same thing.
_RANK = {Severity.QUARANTINE: 0, Severity.REPORT: 1, Severity.NOTE: 2}


class _Outcome(StrEnum):
    MATCH = "match"
    EXTENSION_USED = "extension_used"
    LATER_START = "later_start"
    DEVIATES = "deviates"


@dataclass(frozen=True)
class _Statement:
    """The period facts of one clause or table row, with the dates they state."""

    facts: tuple[Fact, ...]
    starts: tuple[date, ...]
    ends: tuple[date, ...]  # stated, or start + months - 1 day
    extension_months: int | None  # its own, else the longest of the file
    scope: str | None


@dataclass(frozen=True)
class _Register:
    """The register's period a statement is compared with."""

    valid_from: date  # the earliest of the entries
    valid_to: date  # the latest
    max_extension_to: date | None  # the latest "Max förl. till", if any entry has one
    label: str  # for the message: "avtal 23.3-2940-20:026", "delområdet Systemutveckling"
    agreement_number: str | None = None
    page_url: str | None = None

    @classmethod
    def of(
        cls,
        entries: Sequence[RegisterEntry],
        label: str,
        agreement_number: str | None = None,
        page_url: str | None = None,
    ) -> "_Register":
        valid_from, valid_to = _period(entries)
        extensions = [entry.max_extension_to for entry in entries if entry.max_extension_to]
        return cls(
            valid_from=valid_from,
            valid_to=valid_to,
            max_extension_to=max(extensions, default=None),
            label=label,
            agreement_number=agreement_number,
            page_url=page_url,
        )


def run(context: CheckContext) -> list[Finding]:
    """The findings about the periods of the files and of the agreement pages."""
    findings = [finding for file in context.files for finding in _file_findings(file, context)]
    return _one_per_key([*findings, *_page_findings(context)])


def _file_findings(file: CheckedFile, context: CheckContext) -> Iterator[Finding]:
    # A template's dates are no agreement's (see "How" in the module docstring).
    if file.metadata.is_template:
        return
    # A procurement document states the period as it was planned: a deviation is a NOTE.
    procurement_stage = (
        file.metadata.tendsign_cover == _PROCUREMENT_COVER
        or file.metadata.group is DocumentGroup.PROCUREMENT
    )
    for statement in _statements(file.facts):
        for register in _registers(file, statement, context):
            outcome = _outcome(statement, register)
            if outcome not in (_Outcome.MATCH, _Outcome.EXTENSION_USED):
                yield _finding(file, statement, register, outcome, procurement_stage)


def _statements(facts: Sequence[Fact]) -> list[_Statement]:
    """The file's period statements that state a start or an end, in order."""
    grouped: defaultdict[int, list[Fact]] = defaultdict(list)
    for fact in facts:
        if fact.kind in _PERIOD_KINDS and fact.statement is not None:
            grouped[fact.statement].append(fact)
    longest = _longest_extension(facts)
    statements = []
    for group in grouped.values():
        starts = _dates(group, FactKind.PERIOD_START)
        # The end as stated, or else as a length from the start: 14aa1cc8ee3d §9.6.2
        # "Från 2022-12-01 löper ramavtalet därefter under en period av 24 månader".
        ends = _dates(group, FactKind.PERIOD_END) or _ends_after(starts, group)
        if not starts and not ends:
            continue  # a length alone: "löper under en period av 48 månader"
        own = _longest_extension(group)
        statements.append(
            _Statement(
                facts=tuple(group),
                starts=starts,
                ends=ends,
                extension_months=own if own is not None else longest,
                scope=group[0].scope,
            )
        )
    return statements


def _registers(
    file: CheckedFile, statement: _Statement, context: CheckContext
) -> Iterator[_Register]:
    """What the statement is compared with (see "How" in the module docstring)."""
    # A supplier's card: its own agreement, whatever page the card is on. One agreement
    # number per spelling-independent key.
    cards = {
        agreement_key(link.agreement_number) or link.agreement_number: link.agreement_number
        for link in file.links
        if link.agreement_number
    }
    if cards:
        for number in cards.values():
            if entries := context.agreement_entries(number):
                yield _Register.of(entries, f"avtal {number}", agreement_number=number)
        return
    # Any other file: the sub-areas of the pages the statement is about.
    pages = _first_link_per_page(file.links)
    if statement.scope is not None:
        pages = [link for link in pages if _scope_matches(statement.scope, link.page_title)]
    scoped = [(link, entries) for link in pages if (entries := context.page_scope(link))]
    if len({_period(entries) for _, entries in scoped}) != 1:
        return  # no page to compare with, or pages whose sub-areas have different periods
    entries = [entry for _, page_entries in scoped for entry in page_entries]
    if len(scoped) == 1:
        link = scoped[0][0]
        yield _Register.of(entries, f"delområdet {link.page_title}", page_url=link.page_url)
    else:
        yield _Register.of(
            entries, f"delområdena på de {len(scoped)} sidor som länkar till dokumentet"
        )


def _scope_matches(scope: str, page_title: str) -> bool:
    """Whether a statement's scope names the sub-area of a page with this title."""
    if area := _AREA_NUMBER.match(scope):
        # "3." but not "13." or "3.5": the number as a page writes its sub-area's.
        return re.search(rf"(?<![\d.]){area['number']}\.(?!\d)", page_title) is not None
    scope, title = scope.casefold(), page_title.casefold()
    return scope in title or title in scope


def _outcome(statement: _Statement, register: _Register) -> _Outcome:
    """Start and end compared on their own (see "How" in the module docstring)."""
    if any(start > register.valid_from for start in statement.starts):
        return _Outcome.DEVIATES  # the register's agreement started before the stated start
    ends = {_end_outcome(end, statement.extension_months, register) for end in statement.ends}
    if _Outcome.DEVIATES in ends:
        return _Outcome.DEVIATES
    if any(start < register.valid_from for start in statement.starts):
        return _Outcome.LATER_START
    return _Outcome.EXTENSION_USED if _Outcome.EXTENSION_USED in ends else _Outcome.MATCH


def _end_outcome(end: date, extension_months: int | None, register: _Register) -> _Outcome:
    if end == register.valid_to:
        return _Outcome.MATCH
    if extension_months is not None:
        limit = _add_months(end + timedelta(days=1), extension_months) - timedelta(days=1)
    else:
        limit = register.max_extension_to or end
    return _Outcome.EXTENSION_USED if end < register.valid_to <= limit else _Outcome.DEVIATES


def _finding(
    file: CheckedFile,
    statement: _Statement,
    register: _Register,
    outcome: _Outcome,
    procurement_stage: bool,
) -> Finding:
    stated = _stated(statement)
    if statement.scope:
        stated += f" för {statement.scope}"
    compared = f"registret har {register.valid_from} - {register.valid_to} för {register.label}"
    if outcome is _Outcome.LATER_START:
        severity = Severity.NOTE
        message = (
            f"Dokumentet anger avtalsperioden {stated}, och {compared}: "
            "avtalet började senare än dokumentet anger."
        )
    elif procurement_stage:
        severity = Severity.NOTE
        message = (
            f"Dokumentet är från upphandlingen och anger avtalsperioden {stated}, men {compared}. "
            "Ett upphandlingsdokument anger perioden som den var planerad före tilldelningen."
        )
    else:
        severity = Severity.QUARANTINE
        message = (
            f"Dokumentet anger avtalsperioden {stated}{_extension(statement)}, men {compared}."
        )
    return Finding(
        check=CHECK,
        severity=severity,
        # The stated period, with its sub-area: unique within the file, stable across runs.
        subject=f"{statement.scope}: {_stated(statement)}"
        if statement.scope
        else _stated(statement),
        message=message,
        sha256=file.sha256,
        agreement_number=register.agreement_number,
        page_url=register.page_url,
        evidence=" … ".join(dict.fromkeys(fact.raw for fact in statement.facts)),
    )


def _page_findings(context: CheckContext) -> Iterator[Finding]:
    """REPORT for each agreement page whose period differs from its sub-area's in the register."""
    for link in _first_link_per_page(context.links):
        period = parse_period(link.page_period) if link.page_period else None
        entries = context.page_scope(link)
        if period is None or not entries or period == _period(entries):
            continue
        valid_from, valid_to = _period(entries)
        # How many of the sub-area's agreements have other dates than the page: 7 of 19
        # on the Kontorstjänster pages of 23.3-14537-2023 start 2025-04-03, not 2025-04-22.
        agreements: defaultdict[str, list[RegisterEntry]] = defaultdict(list)
        for entry in entries:
            agreements[entry.agreement_number].append(entry)
        other = sum(1 for own in agreements.values() if _period(own) != period)
        yield Finding(
            check=CHECK,
            severity=Severity.REPORT,
            subject=str(link.page_period),
            message=(
                f"Sidans avtalsperiod {link.page_period} stämmer inte med registret, som har "
                f"{valid_from} - {valid_to} för delområdet {link.page_title}: {other} av "
                f"{len(agreements)} avtal har andra datum än sidan."
            ),
            page_url=link.page_url,
            evidence=link.page_period,
        )


def _one_per_key(findings: Sequence[Finding]) -> list[Finding]:
    """One finding per `Finding.key`, the most severe, in the order the keys first came.

    The same stated period can deviate in several statements of a file (the
    Microsoft volume agreement repeats its period in the footer of each page).
    """
    kept: dict[str, Finding] = {}
    for finding in findings:
        earlier = kept.get(finding.key)
        if earlier is None or _RANK[finding.severity] < _RANK[earlier.severity]:
            kept[finding.key] = finding
    return list(kept.values())


def _first_link_per_page(links: Sequence[CatalogLink]) -> list[CatalogLink]:
    """One link per agreement page, in link order: the page's title, numbers and period."""
    pages: dict[str, CatalogLink] = {}
    for link in links:
        pages.setdefault(link.page_url, link)
    return list(pages.values())


def _period(entries: Sequence[RegisterEntry]) -> tuple[date, date]:
    return min(entry.valid_from for entry in entries), max(entry.valid_to for entry in entries)


def _dates(facts: Sequence[Fact], kind: FactKind) -> tuple[date, ...]:
    return tuple(sorted({date.fromisoformat(fact.value) for fact in facts if fact.kind is kind}))


def _ends_after(starts: Sequence[date], facts: Sequence[Fact]) -> tuple[date, ...]:
    """Each start + each PERIOD_MONTHS months - 1 day: 2022-12-01 + 24 months is 2024-11-30."""
    months = {int(fact.value) for fact in facts if fact.kind is FactKind.PERIOD_MONTHS}
    return tuple(
        sorted(
            {_add_months(start, count) - timedelta(days=1) for start in starts for count in months}
        )
    )


def _longest_extension(facts: Sequence[Fact]) -> int | None:
    months = [int(fact.value) for fact in facts if fact.kind is FactKind.EXTENSION_MONTHS]
    return max(months, default=None)


def _add_months(day: date, months: int) -> date:
    """The same day `months` months later, or the last day of a shorter month."""
    year, month = divmod(day.month - 1 + months, 12)
    year += day.year
    return date(year, month + 1, min(day.day, calendar.monthrange(year, month + 1)[1]))


def _stated(statement: _Statement) -> str:
    """The period as the statement gives it: "2022-12-01 - 2024-11-30", "från 2023-02-27"."""
    start = " / ".join(day.isoformat() for day in statement.starts)
    end = " / ".join(day.isoformat() for day in statement.ends)
    if start and end:
        return f"{start} - {end}"
    return f"från {start}" if start else f"till och med {end}"


def _extension(statement: _Statement) -> str:
    if statement.extension_months is None:
        return ""
    if statement.extension_months == 0:
        return " utan förlängning"
    return f" med förlängning högst {statement.extension_months} månader"
