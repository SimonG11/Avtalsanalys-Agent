"""Step-5 check: every register agreement of the run's areas has a main document read.

What:
    `coverage` says for each agreement of the register in the run's framework
    areas whether a main document that goes into the index covers it as the
    agreement's (COVERED), whether only the procurement's version of it or a
    template does (PROCUREMENT_VERSION), whether only documents in quarantine
    do (HELD_BACK), or whether none does (NOT_COVERED). Each covering file
    that does not count carries its reason (`NotCountedFile`). `groups` puts
    the agreements that are not covered for the same reason together, and
    `findings` gives one finding per group: a NOTE for the procurement's
    version, a REPORT for the rest.

Why:
    The assistant (M5) can only answer about an agreement whose terms it has
    read, and the terms are in the main document: the supplier's signed
    "Ramavtal" in its supplier card, or the "Ramavtalets huvuddokument" of the
    sub-area, which holds for every supplier there. An agreement without one
    is a gap the report must show (ADR 0009 decision 6): 6765/05, Volymavtal
    för IBM, has its main document only as
    "ibm-volymavtal-2005-v-1.0-050413.doc", a format step 1 does not fetch. A
    main document the index cannot use is a gap too (the same decision):
    171a3cacf5fd, the Microsoft volume agreement's only main document, is in
    quarantine for Microsoft Ireland's organisation number (`org_numbers`).
    Some main documents go into the index but are not the signed agreement:
    - the procurement's version printed from TendSign, which opens with the
      cover "Upphandlingsdokument" (`DocumentMetadata.tendsign_cover`):
      34d71a7e4da0 p1 "Upphandlingsdokument" "2024-09-24", the only main
      document of Bemanningstjänster, says "avtalsnummer [X]" on p4;
    - a template or draft (`DocumentMetadata.is_template`): 65d611d12eab p7,
      the main document of IT-säkerhet, has "[DATUM (dag-mån-år)]" where the
      signed agreement has its dates (in the pilot it is also in quarantine,
      for the agreement number 23.3-8321-2024-001 it names as its own).
    They hold the agreement's terms, and the signed version is not published
    on avropa.se, so this is how those agreements are read: worth a note, not
    a deviation (PROCUREMENT_VERSION, NOTE). Listed one per agreement, the 33
    Bemanningstjänster agreements with the same printout buried the real gaps,
    so the findings come one per group of agreements and the files that cover
    them.
    In the pilot, with the identity checks' quarantine, 61 of the 121
    agreements are covered, 50 only by the procurement's version (four
    printouts: 34d71a7e4da0 for 33 agreements, 80578a77ea47 for 7,
    64204ca73ffb for 6, and cbe12fd30683 for the 4 agreements of 23.3-2940-20
    whose own supplier cards are in quarantine), 9 only by documents in
    quarantine (65d611d12eab for the 8 of IT-säkerhet, 171a3cacf5fd) and 1
    (6765/05) by none: 4 notes and 3 reports.

How:
    The agreements are the register entries whose framework area is one of the
    run's (`CheckContext.areas`, as the register names them), one per
    agreement key, in register order. A file covers an agreement when it is:
    - a SUPPLIER_AGREEMENT whose link in a supplier card carries the
      agreement's number, compared by key ("23.3.2940-20:018" is
      "23.3-2940-20:018"); or
    - a MAIN_DOCUMENT linked from a page whose scope has the agreement
      (`CheckContext.page_scope`: the register entries of the page's
      procurements in the sub-area the page is titled after; none when the
      title is no sub-area, which `agreement_period` reports). 23.3-1688-2024
      has two pages with different main documents, 0692da436391 for
      "IT-konsulttjänster 1. Verksamhetens IT-behov" and e31f81c753c7 for "5.
      IT-konsultlösningar", each for its own 9 agreements.
    Other types do not cover: IBM's licence terms and amendments on its page
    hold no agreement terms of Kammarkollegiet's. A covering file counts
    unless `not_counted_reason` gives a reason; a file with some sections in
    quarantine still counts, since the rest of it is indexed. The status is
    the best the covering files give: COVERED, then PROCUREMENT_VERSION, then
    HELD_BACK. A file in quarantine never helps, also when it is a template.

    A group is the agreements of one status covered by the same files: the
    indexed ones for PROCUREMENT_VERSION, the quarantined ones for HELD_BACK.
    An agreement NOT_COVERED is a group of its own, and its finding's subject
    is its agreement number. The subject of the other groups' findings is the
    procurement numbers of their agreements, however many there are, and
    `Finding.key` holds the severity (NOTE for PROCUREMENT_VERSION, REPORT for
    HELD_BACK). So the key survives a run in which an agreement joins or
    leaves the group, also when one agreement is left, and an acceptance of
    the procurement's version does not carry over to the same procurement's
    agreements held back. Two groups of one severity with the same subject
    are told apart by the hashes of their files; the subjects of both then
    change when the second group appears.
"""

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from avtalsagent.domain.extracted import (
    DocumentMetadata,
    DocumentType,
    Finding,
    Quarantine,
    Severity,
)
from avtalsagent.domain.identifiers import agreement_key
from avtalsagent.domain.register import RegisterEntry
from avtalsagent.ingestion.checks.context import CheckContext, CheckedFile

CHECK = "coverage"

# The cover of a document printed from TendSign at the procurement stage
# (`extract/document_type.tendsign_cover`); "Ramavtal" is the cover at the agreement stage.
_PROCUREMENT_COVER = "Upphandlingsdokument"


class CoverageStatus(StrEnum):
    """How an agreement is covered, from best to worst."""

    COVERED = "covered"  # a main document indexed as the agreement's covers it
    # Only main documents that are indexed but are the procurement's version or a template.
    PROCUREMENT_VERSION = "procurement_version"
    HELD_BACK = "held_back"  # only documents in quarantine cover it
    NOT_COVERED = "not_covered"  # no main document read covers it


class NotCounted(StrEnum):
    """Why a file that covers an agreement does not count as its main document."""

    QUARANTINED = "quarantined"  # kept out of the index
    TENDSIGN_PRINTOUT = "tendsign_printout"  # the procurement's version, printed from TendSign
    TEMPLATE = "template"  # a template or draft (`DocumentMetadata.is_template`)


# The severity of a group's finding: the procurement's version is read and indexed.
_SEVERITY: dict[CoverageStatus, Severity] = {
    CoverageStatus.PROCUREMENT_VERSION: Severity.NOTE,
    CoverageStatus.HELD_BACK: Severity.REPORT,
    CoverageStatus.NOT_COVERED: Severity.REPORT,
}

# The reasons as the messages and the report write them.
NOT_COUNTED_NAMES: dict[NotCounted, str] = {
    NotCounted.QUARANTINED: "i karantän",
    NotCounted.TENDSIGN_PRINTOUT: "upphandlingens version från TendSign",
    NotCounted.TEMPLATE: "mall eller utkast",
}


@dataclass(frozen=True)
class NotCountedFile:
    """A file that covers an agreement but does not count as its main document."""

    sha256: str
    reason: NotCounted
    own: bool  # the agreement's own supplier agreement (supplier card); else an area document


@dataclass(frozen=True)
class AgreementCoverage:
    """How one register agreement is covered by the documents read."""

    agreement_number: str  # as the register writes it
    procurement_number: str
    framework_area: str
    supplier_name: str
    status: CoverageStatus
    cards: tuple[str, ...]  # sha256 of its own supplier agreements (supplier card) that count
    main_documents: tuple[str, ...]  # sha256 of the area main documents that count
    # The files that cover it but do not count, whatever the status: its own supplier
    # agreements first, then the area main documents.
    not_counted: tuple[NotCountedFile, ...]

    @property
    def procurement_versions(self) -> tuple[str, ...]:
        """The covering files that are indexed but are the procurement's version or a template."""
        return tuple(f.sha256 for f in self.not_counted if f.reason is not NotCounted.QUARANTINED)

    @property
    def held_back(self) -> tuple[str, ...]:
        """The covering files in quarantine."""
        return tuple(f.sha256 for f in self.not_counted if f.reason is NotCounted.QUARANTINED)


@dataclass(frozen=True)
class CoverageGroup:
    """Agreements that are not covered, for the same reason: one finding."""

    status: CoverageStatus  # never COVERED
    # The files that cover them: the procurement's versions, or the files in quarantine;
    # () for an agreement NOT_COVERED.
    files: tuple[str, ...]
    agreements: tuple[AgreementCoverage, ...]  # in register order
    subject: str  # the finding's subject


def coverage(context: CheckContext, quarantine: Quarantine) -> list[AgreementCoverage]:
    """How each register agreement of the run's areas is covered, in register order."""
    cards, main_documents = _covering_files(context)
    coverages: list[AgreementCoverage] = []
    for key, entry in _agreements(context).items():
        own = cards.get(key, [])
        area = main_documents.get(key, [])
        reasons = {f.sha256: not_counted_reason(f.metadata, quarantine) for f in own + area}
        own_shas = {f.sha256 for f in own}
        not_counted = tuple(
            NotCountedFile(sha256, reason, own=sha256 in own_shas)
            for sha256, reason in reasons.items()
            if reason is not None
        )
        counted_cards = tuple(f.sha256 for f in own if reasons[f.sha256] is None)
        counted_main = tuple(f.sha256 for f in area if reasons[f.sha256] is None)
        if counted_cards or counted_main:
            status = CoverageStatus.COVERED
        # The terms are read, from the procurement's version: 34d71a7e4da0 for Bemanningstjänster.
        elif any(f.reason is not NotCounted.QUARANTINED for f in not_counted):
            status = CoverageStatus.PROCUREMENT_VERSION
        # Nothing of the agreement's is read: 171a3cacf5fd, Microsoft's, is in quarantine.
        elif not_counted:
            status = CoverageStatus.HELD_BACK
        else:
            status = CoverageStatus.NOT_COVERED
        coverages.append(
            AgreementCoverage(
                agreement_number=entry.agreement_number,
                procurement_number=entry.procurement_number,
                framework_area=entry.framework_area,
                supplier_name=entry.supplier_name,
                status=status,
                cards=counted_cards,
                main_documents=counted_main,
                not_counted=not_counted,
            )
        )
    return coverages


def not_counted_reason(metadata: DocumentMetadata, quarantine: Quarantine) -> NotCounted | None:
    """Why a covering file does not count as the agreement's main document; None when it does.

    The first reason that applies: quarantine, the procurement's version, a
    template or draft. The procurement's version comes before the template,
    since it says more: the printouts are also drafts ("[DATUM]").
    """
    # Kept out of the index: 171a3cacf5fd (Microsoft Ireland's organisation number).
    if quarantine.holds(metadata.sha256):
        return NotCounted.QUARANTINED
    # The tendered text: 34d71a7e4da0 p1 "Upphandlingsdokument", p4 "avtalsnummer [X]".
    if metadata.tendsign_cover == _PROCUREMENT_COVER:
        return NotCounted.TENDSIGN_PRINTOUT
    # Unfilled dates: 65d611d12eab p7 "[DATUM (dag-mån-år)]".
    if metadata.is_template:
        return NotCounted.TEMPLATE
    return None


def groups(coverages: Sequence[AgreementCoverage]) -> list[CoverageGroup]:
    """The agreements not covered, grouped by status and covering files; best status first."""
    grouped: dict[tuple[CoverageStatus, tuple[str, ...], str], list[AgreementCoverage]] = {}
    for item in coverages:
        if item.status is CoverageStatus.COVERED:
            continue
        files = {
            CoverageStatus.PROCUREMENT_VERSION: item.procurement_versions,
            CoverageStatus.HELD_BACK: item.held_back,
        }.get(item.status, ())
        # Nothing ties two agreements without a main document together: one group each.
        alone = item.agreement_number if item.status is CoverageStatus.NOT_COVERED else ""
        grouped.setdefault((item.status, files, alone), []).append(item)
    order = list(CoverageStatus)
    keys = sorted(grouped, key=lambda key: order.index(key[0]))  # stable: register order
    subjects = {key: _subject(key[0], grouped[key]) for key in keys}
    repeated = Counter((_SEVERITY[key[0]], subject) for key, subject in subjects.items())
    result: list[CoverageGroup] = []
    for key in keys:
        status, files, _ = key
        subject = subjects[key]
        # Two groups of the same procurement and severity, such as the two sub-areas of
        # 23.3-1688-2024 if both their main documents were printouts, are told apart by
        # their files, so that an acceptance in accepted_findings.toml covers one group only.
        if repeated[_SEVERITY[status], subject] > 1 and files:
            subject += f" ({', '.join(sha[:12] for sha in files)})"
        result.append(CoverageGroup(status, files, tuple(grouped[key]), subject))
    return result


def finding(group: CoverageGroup) -> Finding:
    """The finding of one group: a NOTE for the procurement's version, else a REPORT."""
    one = group.agreements[0] if len(group.agreements) == 1 else None
    return Finding(
        check=CHECK,
        severity=_SEVERITY[group.status],
        subject=group.subject,
        message=_message(group),
        agreement_number=one.agreement_number if one else None,
    )


def findings(coverages: Sequence[AgreementCoverage]) -> list[Finding]:
    """One finding per group of agreements that no indexed main document of their own covers."""
    return [finding(group) for group in groups(coverages)]


def _agreements(context: CheckContext) -> dict[str, RegisterEntry]:
    """The first register entry of each agreement of the run's areas, by agreement key."""
    agreements: dict[str, RegisterEntry] = {}
    for entry in context.register:
        if entry.framework_area in context.areas:
            key = agreement_key(entry.agreement_number) or entry.agreement_number
            agreements.setdefault(key, entry)
    return agreements


def _covering_files(
    context: CheckContext,
) -> tuple[dict[str, list[CheckedFile]], dict[str, list[CheckedFile]]]:
    """The supplier agreements and the area main documents of each agreement key."""
    cards: dict[str, list[CheckedFile]] = {}
    main_documents: dict[str, list[CheckedFile]] = {}
    for file in context.files:
        # A supplier's own agreement: ee6107229c37, "Ramavtal" in the card of 23.3-2940-20:018.
        if file.metadata.document_type is DocumentType.SUPPLIER_AGREEMENT:
            numbers = [link.agreement_number for link in file.links if link.agreement_number]
            target = cards
        # The sub-area's: 0692da436391 on "IT-konsulttjänster 1. Verksamhetens IT-behov".
        elif file.metadata.document_type is DocumentType.MAIN_DOCUMENT:
            numbers = [
                entry.agreement_number for link in file.links for entry in context.page_scope(link)
            ]
            target = main_documents
        else:
            continue
        for key in dict.fromkeys(agreement_key(number) for number in numbers):
            if key:
                target.setdefault(key, []).append(file)
    return cards, main_documents


def _subject(status: CoverageStatus, agreements: Sequence[AgreementCoverage]) -> str:
    """The agreement number of an agreement NOT_COVERED, else the procurement numbers."""
    if status is CoverageStatus.NOT_COVERED:
        return agreements[0].agreement_number
    return ", ".join(sorted({item.procurement_number for item in agreements}))


def _message(group: CoverageGroup) -> str:
    items = group.agreements
    if len(items) == 1:
        item = items[0]
        lead = f"Avtal {item.agreement_number} ({item.supplier_name}, {item.framework_area})"
    else:
        areas = list(dict.fromkeys(item.framework_area for item in items))
        lead = f"{len(items)} avtal inom {_join(areas)}"
    reasons = {f.sha256: f.reason for item in items for f in item.not_counted}
    files = _join([f"{sha[:12]} ({NOT_COUNTED_NAMES[reasons[sha]]})" for sha in group.files])
    if group.status is CoverageStatus.PROCUREMENT_VERSION:
        text = (
            f"{lead} täcks bara av huvuddokument som indexeras men inte är det undertecknade "
            f"avtalet: {files}. Den undertecknade versionen publiceras inte på avropa.se."
            + _also_held_back(items)
        )
    elif group.status is CoverageStatus.HELD_BACK:
        text = f"{lead} täcks bara av huvuddokument som inte indexeras: {files}."
    else:
        text = (
            f"{lead} har inget inläst huvuddokument: inget leverantörskort med avtalets "
            "nummer och inget huvuddokument på en sida för avtalets delområde."
        )
    if len(items) > 1:
        text += " Avtalen: " + ", ".join(sorted(item.agreement_number for item in items)) + "."
    return text


def _also_held_back(items: Sequence[AgreementCoverage]) -> str:
    """The files in quarantine that would also cover the agreements, as a sentence or two.

    In the pilot, the ÅF and Tieto cards of 23.3-2940-20 (`supplier_party`),
    whose agreements are read from the printout cbe12fd30683.
    """
    held = [f for item in items for f in item.not_counted if f.reason is NotCounted.QUARANTINED]
    own = list(dict.fromkeys(f.sha256[:12] for f in held if f.own))
    other = list(dict.fromkeys(f.sha256[:12] for f in held if not f.own))
    text = ""
    if own:
        whose = "Avtalets eget" if len(items) == 1 else "Avtalens egna"
        text += f" {whose} leverantörsavtal ligger i karantän: {_join(own)}."
    if other:
        text += f" Också i karantän: {_join(other)}."
    return text


def _join(parts: Iterable[str]) -> str:
    """'a', 'a och b', 'a, b och c'."""
    items = list(parts)
    if len(items) < 2:
        return "".join(items)
    return f"{', '.join(items[:-1])} och {items[-1]}"
