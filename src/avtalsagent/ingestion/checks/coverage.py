"""Step-5 check: every register agreement of the run's areas has a main document read.

What:
    `coverage` says for each agreement of the register in the run's framework
    areas whether a main document that goes into the index covers it
    (COVERED), whether only documents that are held back do (HELD_BACK), or
    whether none does (NOT_COVERED). `findings` gives one REPORT finding per
    agreement that is not covered. `held_back_reason` says why a main
    document does not count, for the message and the report.

Why:
    The assistant (M5) can only answer about an agreement whose terms it has
    read, and the terms are in the main document: the supplier's signed
    "Ramavtal" in its supplier card, or the "Ramavtalets huvuddokument" of the
    sub-area, which holds for every supplier there. An agreement without one
    is a gap the report must show (M4 design §3): 6765/05, Volymavtal för IBM,
    has its main document only as "ibm-volymavtal-2005-v-1.0-050413.doc", a
    format step 1 does not fetch (M4 survey, doctypes.md §7). Nor does a main
    document the index cannot use as the agreement's (critique, data item 8):
    - one in quarantine: 171a3cacf5fd, the Microsoft volume agreement's only
      main document, names Microsoft Ireland's organisation number where the
      register has Microsoft AB (`org_numbers`);
    - a template or draft (`DocumentMetadata.is_template`): 65d611d12eab p7,
      the main document of IT-säkerhet, has "[DATUM (dag-mån-år)]" where the
      signed agreement has its dates;
    - the procurement's version printed from TendSign, which opens with the
      cover "Upphandlingsdokument" (`DocumentMetadata.tendsign_cover`):
      34d71a7e4da0 p1 "Upphandlingsdokument" "2024-09-24", the only main
      document of Bemanningstjänster, says "avtalsnummer [X]" on p4. It is
      what suppliers tendered on, not what they signed.
    Templates and printouts are still indexed; they only do not count as the
    agreement's main document. Such an agreement is HELD_BACK, with the
    documents that would have covered it, so a person can see what is missing.
    In the pilot, with the identity checks' quarantine, 61 of the 121
    agreements are covered, 59 held back and 1 (6765/05) not covered. 54 of
    the 59 are all agreements of four procurements whose only main document
    is a printout or a template (34d71a7e4da0, 64204ca73ffb, 80578a77ea47,
    65d611d12eab). Counting every main document read, all but 6765/05 have
    one, as in the survey.

How:
    The agreements are the register entries whose framework area is one of the
    run's (`CheckContext.areas`, as the register names them), one per
    agreement key, in register order. A file covers an agreement when it is:
    - a SUPPLIER_AGREEMENT whose link in a supplier card carries the
      agreement's number, compared by key ("23.3.2940-20:018" is
      "23.3-2940-20:018"); or
    - a MAIN_DOCUMENT linked from a page whose scope has the agreement
      (`CheckContext.page_scope`: the register entries of the page's
      procurements in the sub-area the page is titled after). 23.3-1688-2024
      has two pages with different main documents, 0692da436391 for
      "IT-konsulttjänster 1. Verksamhetens IT-behov" and e31f81c753c7 for "5.
      IT-konsultlösningar", each for its own 9 agreements.
    Other types do not cover: IBM's licence terms and amendments on its page
    hold no agreement terms of Kammarkollegiet's. A covering file counts
    unless `held_back_reason` gives a reason; a file with some sections in
    quarantine still counts, since the rest of it is indexed.
    `AgreementCoverage.held_back` lists the covering files that do not count
    whatever the status, so a covered agreement still shows them.
"""

from collections import defaultdict
from collections.abc import Sequence
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
    COVERED = "covered"  # a main document that goes into the index covers it
    HELD_BACK = "held_back"  # only templates, drafts or quarantined documents cover it
    NOT_COVERED = "not_covered"  # no main document read covers it


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
    # The files that would cover it but are templates, drafts or quarantined.
    held_back: tuple[str, ...]


def coverage(context: CheckContext, quarantine: Quarantine) -> list[AgreementCoverage]:
    """How each register agreement of the run's areas is covered, in register order."""
    cards, main_documents = _covering_files(context)
    coverages: list[AgreementCoverage] = []
    for key, entry in _agreements(context).items():
        own = cards.get(key, [])
        area = main_documents.get(key, [])
        counts = {f.sha256: held_back_reason(f.metadata, quarantine) is None for f in own + area}
        counted_cards = tuple(f.sha256 for f in own if counts[f.sha256])
        counted_main = tuple(f.sha256 for f in area if counts[f.sha256])
        held_back = tuple(sha for sha, counted in counts.items() if not counted)
        if counted_cards or counted_main:
            status = CoverageStatus.COVERED
        elif held_back:
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
                held_back=held_back,
            )
        )
    return coverages


def findings(coverages: list[AgreementCoverage]) -> list[Finding]:
    """One REPORT finding per agreement that no indexed main document covers."""
    return [
        Finding(
            check=CHECK,
            severity=Severity.REPORT,
            subject=item.agreement_number,
            message=_message(item),
            agreement_number=item.agreement_number,
        )
        for item in coverages
        if item.status is not CoverageStatus.COVERED
    ]


def held_back_reason(metadata: DocumentMetadata, quarantine: Quarantine) -> str | None:
    """Why a main document does not count for coverage, in Swedish; None when it counts.

    The first reason that applies: quarantine, the procurement's version, a
    template or draft. The procurement's version comes before the template,
    since it says more: the printouts are also drafts ("[DATUM]").
    """
    # Kept out of the index: 171a3cacf5fd (Microsoft Ireland's organisation number).
    if quarantine.holds(metadata.sha256):
        return "i karantän"
    # The tendered text: 34d71a7e4da0 p1 "Upphandlingsdokument", p4 "avtalsnummer [X]".
    if metadata.tendsign_cover == _PROCUREMENT_COVER:
        return "upphandlingens version från TendSign"
    # Unfilled dates: 65d611d12eab p7 "[DATUM (dag-mån-år)]".
    if metadata.is_template:
        return "mall eller utkast"
    return None


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
    cards: defaultdict[str, list[CheckedFile]] = defaultdict(list)
    main_documents: defaultdict[str, list[CheckedFile]] = defaultdict(list)
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
                target[key].append(file)
    return dict(cards), dict(main_documents)


def _message(item: AgreementCoverage) -> str:
    agreement = f"Avtal {item.agreement_number} ({item.supplier_name}, {item.framework_area})"
    if item.status is CoverageStatus.HELD_BACK:
        return (
            f"{agreement} täcks bara av huvuddokument som inte räknas ({_files(item.held_back)}): "
            "ett huvuddokument räknas inte när det är i karantän, en mall eller ett utkast, "
            "eller upphandlingens version från TendSign."
        )
    return (
        f"{agreement} har inget inläst huvuddokument: inget leverantörskort med avtalets "
        "nummer och inget huvuddokument på en sida för avtalets delområde."
    )


def _files(shas: Sequence[str]) -> str:
    """The files as the report names them, by the first 12 characters of their hash."""
    prefixes = [sha[:12] for sha in shas]
    if len(prefixes) == 1:
        return prefixes[0]
    return f"{', '.join(prefixes[:-1])} och {prefixes[-1]}"
