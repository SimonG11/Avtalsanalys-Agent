"""The ingestion report: what one run of steps 3-5 read, found and held back.

What:
    `build_report` sums up an `IngestionResult` (`ingestion/pipeline.py`) and the
    run's `RunInfo` in an `IngestionReport`: the run (time, register edition,
    framework areas, language model), the documents by type, the sections and
    chunks, how each file's own numbers compare with the register, how the
    register's agreements are covered, the share of resolved references, every
    finding, what is held back in quarantine, and the accepted deviations.
    `render_markdown` writes the report for a person, in Swedish, and
    `render_json` the same numbers for a program. `write_report` saves both as
    `<start>-inlasning.md` and `.json`.

Why:
    The report is how a run is reviewed ("en inläsningsrapport per körning",
    architecture plan, section 4), and the three done-when criteria of M4 are
    read from it: it shows coverage and deviations, it lists the documents in
    quarantine with the reason, and it states the share of resolved references.
    The model holds numbers and lists only, so tests check the content without
    reading text, and two runs can be compared in their JSON. The report reads
    what the steps concluded and repeats no check: the agreements that are not
    covered are grouped and explained as the coverage check does it
    (`coverage.groups`, `AgreementCoverage.not_counted`).

How:
    - A file is named by the title step 4 gave it (the link text used most
      often), with the agreement number for a supplier's own agreement, and the
      first 12 characters of its hash: many files share a title (25 are called
      "Ramavtal"), and the hash names the file in data/documents/ and in every
      command. Where the title alone does not say which file it is (the
      templates, the main documents of the coverage groups: 10 files are called
      "Ramavtalets huvuddokument"), the titles of its pages follow. A page is
      named by its title, from the run's links.
    - The model and the JSON keep the enum values as the database stores them;
      the markdown gives them Swedish names (`DOCUMENT_TYPE_NAMES` and the
      other tables below).
    - Numbers are written the Swedish way: a space between thousands ("13 175"),
      a decimal comma and a space before the percent sign ("74,4 %"). The space
      is a no-break space, so a number never breaks over two lines. A count of
      one takes the singular ("1 fil", "1 hänvisning").
    - Nothing personal is printed. Findings carry no names or contact details
      (the checks see to that), and of an acceptance in accepted_findings.toml
      only the reason and date are shown, not the reviewer.
    - The share of resolved references is `reference_resolver.resolution_rate`
      (decision 5 of the M4 design). The report counts what the rate leaves
      out, by kind first (laws, "fråga N") and then by status among the rest
      (list items, self-references, other external ones), so the formula it
      prints adds up to the rate's denominator.
    - Every finding is printed with its key (`Finding.key`), which is what a
      person writes in accepted_findings.toml to accept it. Findings are listed
      by severity and check. The coverage check gives one finding per group of
      agreements (33 in the pilot have only the same TendSign printout), named
      by the group's procurement: "Avtal i 23.3-14537-2023".
"""

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from avtalsagent.domain.extracted import (
    DOCUMENT_GROUPS,
    DocumentGroup,
    DocumentMetadata,
    DocumentType,
    Finding,
    Reference,
    ReferenceKind,
    ReferenceStatus,
    Severity,
)
from avtalsagent.domain.register import RegisterVersion
from avtalsagent.ingestion.checks import (
    agreement_number,
    agreement_period,
    coverage,
    document_type,
    missing_text,
    org_numbers,
    procurement_number,
    still_published,
    supplier_party,
)
from avtalsagent.ingestion.checks.coverage import (
    NOT_COUNTED_NAMES,
    AgreementCoverage,
    CoverageGroup,
    CoverageStatus,
    NotCounted,
)
from avtalsagent.ingestion.checks.procurement_number import NumberStatus
from avtalsagent.ingestion.extract.document_type import FALLBACK_RULES, NO_RULE
from avtalsagent.ingestion.extract.reference_resolver import counts_in_rate, resolution_rate
from avtalsagent.ingestion.extract.title_matcher import TITLE_RULE, TOPIC_RULE, MatchStats
from avtalsagent.ingestion.pipeline import IngestionResult
from avtalsagent.ingestion.step3_chunk import OutlineKind
from avtalsagent.ingestion.step5_validate import DOCUMENT_CHECKS, Validation

REPORT_SUFFIX = "-inlasning"  # data/reports/2026-10-06T140312-inlasning.md
# Where a person accepts a deviation (step5_validate): the default of the setting
# ACCEPTED_FINDINGS_FILE, a path from the repository root.
ACCEPTED_FILE = Path("accepted_findings.toml")

_FALLBACKS = FALLBACK_RULES | {NO_RULE}  # type rules listed last
_NBSP = "\u00a0"  # between thousands and before "%": "13 175", "74,4 %"
_SHOWN_NUMBERS = 10  # numbers a table of contents lists without a section, shown per file
_EVIDENCE_CHARS = 300  # the evidence of a finding is cut after this many characters

# --- Swedish names -------------------------------------------------------------------------

# The names of the architecture plan (3.2), as the documents call themselves.
DOCUMENT_TYPE_NAMES: dict[DocumentType, str] = {
    DocumentType.SUPPLIER_AGREEMENT: "Leverantörsavtal",
    DocumentType.MAIN_DOCUMENT: "Huvuddokument",
    DocumentType.GENERAL_TERMS: "Allmänna villkor",
    DocumentType.ANNEX: "Bilaga",
    DocumentType.PRICE_ANNEX: "Prisbilaga",
    DocumentType.REQUIREMENTS_CATALOGUE: "Kravkatalog",
    DocumentType.REQUIREMENTS_SPECIFICATION: "Kravspecifikation",
    DocumentType.AMENDMENT: "Ändring",
    DocumentType.LICENCE_TERMS: "Licensvillkor",
    DocumentType.PROCUREMENT_DOCUMENT: "Upphandlingsdokument",
    DocumentType.QUESTIONS_AND_ANSWERS: "Frågor och svar",
    DocumentType.CALL_OFF_GUIDANCE: "Avropsstöd",
    DocumentType.REQUIREMENTS_REPORT: "Kravredovisning",
    DocumentType.TEMPLATE: "Mall",
    DocumentType.UNKNOWN: "Okänd",
}

GROUP_NAMES: dict[DocumentGroup, str] = {
    DocumentGroup.AGREEMENT: "avtalet",
    DocumentGroup.PROCUREMENT: "upphandlingen",
    DocumentGroup.SUPPORT: "stöd för avrop",
}

OUTLINE_NAMES: dict[OutlineKind, str] = {
    OutlineKind.NUMBERED: "Numrerade rubriker",
    OutlineKind.QUESTIONS: "Frågor och svar, ett avsnitt per fråga",
    OutlineKind.HEADINGS: "Rubriker utan nummer",
    OutlineKind.NONE: "Inga rubriker, hela filen är ett avsnitt",
}

NUMBER_STATUS_NAMES: dict[NumberStatus, str] = {
    NumberStatus.MATCHES: "Numren hör till upphandlingen på filens avtalssidor",
    NumberStatus.NO_NUMBER: "Inget diarie- eller avtalsnummer i texten",
    NumberStatus.CASE_MANAGEMENT_ONLY: "Bara ärendenummer (23.5-serien, 96-15-2015)",
    NumberStatus.DEVIATES: "Avviker",
}

REFERENCE_KIND_NAMES: dict[ReferenceKind, str] = {
    ReferenceKind.SECTION_NUMBER: "Avsnittsnummer (”punkt 6.21.9”)",
    ReferenceKind.SECTION_TITLE: "Avsnittsrubrik (”avsnitt Avtalsbrott och påföljder”)",
    ReferenceKind.DOCUMENT: "Dokumentnamn (”Allmänna villkor”)",
    ReferenceKind.ANNEX_NUMBER: "Bilaga med nummer (”bilaga 3”)",
    ReferenceKind.ANNEX_NAME: "Bilaga med namn (”bilaga Priser”)",
    ReferenceKind.QUESTION: "Fråga i en frågelogg (”fråga 12”)",
    ReferenceKind.LAW: "Lag, förordning eller standard",
}

REFERENCE_STATUS_NAMES: dict[ReferenceStatus, str] = {
    ReferenceStatus.RESOLVED: "Upplöst",
    ReferenceStatus.SELF: "Självhänvisning",
    ReferenceStatus.LIST_ITEM: "Listpunkt (inget avsnitt)",
    ReferenceStatus.NOT_PUBLISHED: "Ej publicerad på avtalssidan",
    ReferenceStatus.AMBIGUOUS: "Flertydig",
    ReferenceStatus.NUMBER_MISSING: "Numret saknas i dokumentet",
    ReferenceStatus.TITLE_MISSING: "Rubriken saknas i dokumentet",
    ReferenceStatus.PLACEHOLDER: "Mallfält",
    ReferenceStatus.EXTERNAL: "Extern (lag eller standard)",
}

# The resolver's rules (ingestion/extract/reference_resolver.py, title_matcher.py), in the
# order the report lists them.
REFERENCE_RULE_NAMES: dict[str, str] = {
    "R1": "avsnittsnummer i samma fil",
    "R1q": "nummer i en frågelogg, i upphandlingsdokumentet frågan gäller",
    "R1x": "nummer i ett namngivet dokument (”punkt 6.19.7 i Allmänna villkor”)",
    "R2": "namngivet dokument, bland filerna på avtalssidan",
    TOPIC_RULE: "ämne i ett namngivet dokument, avsnittet valt av språkmodellen",
    "R3": "bilaga med nummer, bland filerna på avtalssidan",
    "R4": "avsnittsrubrik i samma fil eller i en annan fil på sidan",
    "R4q": "rubrik i en frågelogg, i upphandlingsdokumentet frågan gäller",
    TITLE_RULE: "avsnittsrubrik som reglerna inte hittade, vald av språkmodellen",
    "R5": "bilaga med namn, bland filerna på avtalssidan",
    "RQ": "”fråga N” i samma frågelogg",
}
_TEXT_RULE = "texten avgör (lag, mallfält, listpunkt, självhänvisning)"  # Reference.rule None

# The statuses of the coverage check, as the report writes them after a count ("1 avtal är
# täckt") or capitalised at the start of a line.
COVERAGE_STATUS_NAMES: dict[CoverageStatus, str] = {
    CoverageStatus.COVERED: "täckt",
    CoverageStatus.PROCUREMENT_VERSION: "bara upphandlingens version",
    CoverageStatus.HELD_BACK: "bara dokument i karantän",
    CoverageStatus.NOT_COVERED: "inte täckt",
}

SEVERITY_NAMES: dict[Severity, str] = {
    Severity.QUARANTINE: "Karantän",
    Severity.REPORT: "Rapport",
    Severity.NOTE: "Notering",
}

# Each check's name and what it compares, in the order step 5 runs them.
CHECK_NAMES: dict[str, tuple[str, str]] = {
    procurement_number.CHECK: (
        "Diarienummer",
        "Dokumentets eget diarienummer hör till upphandlingen på sidan som länkar till det.",
    ),
    agreement_number.CHECK: (
        "Avtalsnummer",
        "Ett leverantörsavtal har leverantörskortets avtalsnummer, och ett gemensamt "
        "dokument anger ingen enskild leverantörs nummer.",
    ),
    org_numbers.CHECK: (
        "Organisationsnummer",
        "Varje organisationsnummer i dokumentet tillhör en leverantör i upphandlingen.",
    ),
    supplier_party.CHECK: (
        "Leverantör i avtalet",
        "Leverantören i ett leverantörsavtal är den som registret har för avtalet.",
    ),
    agreement_period.CHECK: (
        "Avtalsperiod",
        "Avtalsperioden i dokumentet och på avtalssidan stämmer med registret.",
    ),
    document_type.CHECK: (
        "Dokumenttyp",
        "En regel för länktexten har gett filen dess dokumenttyp.",
    ),
    missing_text.CHECK: (
        "Text saknas",
        "Filens sidor har ett textlager, så att texten kan läsas.",
    ),
    still_published.CHECK: (
        "Fortfarande publicerad",
        "Någon sida på avropa.se länkar fortfarande till filen.",
    ),
    coverage.CHECK: (
        "Täckning",
        "Varje avtal i registret har ett inläst huvuddokument.",
    ),
}

# --- The run -------------------------------------------------------------------------------


@dataclass(frozen=True)
class RunInfo:
    """What the caller knows about the run that the result does not hold."""

    started_at: datetime  # timezone-aware
    finished_at: datetime  # timezone-aware
    register_version: RegisterVersion | None  # the edition compared with; None if none loaded
    areas: tuple[str, ...]  # the framework areas of the run, as the register names them
    model: str | None  # the language model that chose headings; None when it did not run
    accepted_file: Path = ACCEPTED_FILE  # settings.accepted_findings_file, named in the report

    def __post_init__(self) -> None:
        for moment in (self.started_at, self.finished_at):
            if moment.utcoffset() is None:
                raise ValueError(f"the run's times must be timezone-aware, got {moment}")


# --- The report ----------------------------------------------------------------------------


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class FileRef(_Frozen):
    """A file as the report names it."""

    sha256: str
    title: str | None  # DocumentMetadata.title; None for a file step 4 did not read
    document_type: DocumentType | None = None
    agreement_number: str | None = None  # set for a supplier's own agreement
    pages: tuple[str, ...] = ()  # the titles of the pages that link to it, each once


class RunSummary(_Frozen):
    """Körning."""

    started_at: datetime
    finished_at: datetime
    register_version: RegisterVersion | None
    areas: tuple[str, ...]
    model: str | None
    matching: MatchStats | None  # what the language model was asked; None when it did not run
    accepted_file: str  # where the acceptances were read from (RunInfo.accepted_file)


class TypeCount(_Frozen):
    document_type: DocumentType
    files: int
    rules: dict[str, int]  # files per type rule, e.g. {"R05": 12}


class OcrFile(_Frozen):
    file: FileRef
    pages: tuple[int, ...]  # the PDF pages without a text layer
    page_count: int


class DocumentSummary(_Frozen):
    """Dokument."""

    files: int
    by_file_type: dict[str, int]  # {"pdf": 176, "docx": 31}
    pdf_pages: int
    by_type: tuple[TypeCount, ...]  # in the order of DocumentType; types without files left out
    templates: tuple[FileRef, ...]  # DocumentMetadata.is_template
    ocr_files: tuple[OcrFile, ...]
    unlinked: tuple[str, ...]  # sha256 of parsed files no page links to any more


class ContentsGap(_Frozen):
    file: FileRef
    missing: tuple[str, ...]  # numbers the table of contents lists that are no section


class SectionSummary(_Frozen):
    """Avsnitt och chunkar."""

    sections: int
    chunks: int
    by_outline: dict[OutlineKind, int]
    without_sections: tuple[FileRef, ...]  # files step 3 found no text in
    with_contents: int  # files with a table of contents
    contents_gaps: tuple[ContentsGap, ...]


class DeviatingFile(_Frozen):
    file: FileRef
    numbers: dict[str, Severity]  # the numbers the procurement_number check found, by severity


class RegisterMatch(_Frozen):
    """Stämmer med registret: each file's own numbers next to its pages' procurements."""

    by_status: dict[NumberStatus, int]
    deviating: tuple[DeviatingFile, ...]


class AreaCoverage(_Frozen):
    framework_area: str
    agreements: int
    covered: int
    by_card: int  # covered by its own supplier agreement only
    by_main_document: int  # covered by the sub-area's main document only
    by_both: int
    procurement_version: int  # only by the procurement's version of a main document, or a template
    held_back: int  # only by documents in quarantine
    not_covered: int


class NotCountedRef(_Frozen):
    """A file that covers agreements but does not count as their main document, and why."""

    file: FileRef
    reason: NotCounted
    own: bool  # an agreement's own supplier agreement (supplier card); else an area document


class CoverageGap(_Frozen):
    """An agreement no main document indexed as its own covers."""

    agreement_number: str
    procurement_number: str
    framework_area: str
    supplier_name: str


class GapGroup(_Frozen):
    """Agreements not covered for the same reason: one finding of the coverage check."""

    status: CoverageStatus  # not COVERED
    subject: str  # the subject of the group's finding: an agreement or procurement number
    framework_areas: tuple[str, ...]
    # The files that cover them but do not count: those the group is formed by first, then
    # others of its agreements (their own supplier agreements in quarantine).
    files: tuple[NotCountedRef, ...]
    agreements: tuple[CoverageGap, ...]


class CoverageSummary(_Frozen):
    """Täckning."""

    areas: tuple[AreaCoverage, ...]  # the run's areas first, in their order
    groups: tuple[GapGroup, ...]  # by status, then in the order of the areas


class ReferenceTally(_Frozen):
    """References counted for the rate (`reference_resolver.counts_in_rate`)."""

    references: int
    counted: int  # in the denominator of the rate
    resolved: int  # ...and RESOLVED
    statuses: dict[ReferenceStatus, int]  # all of them by status; a status with none left out


class KindTally(ReferenceTally):
    kind: ReferenceKind


class RuleTally(ReferenceTally):
    rule: str | None  # Reference.rule; None when the text alone decided the status


class ReferenceSummary(_Frozen):
    """Hänvisningar: the rate of decision 5 of the M4 design and what it is made of."""

    references: int
    # Left out of the rate, in this order: by kind, then by status among the rest.
    laws: int  # kind LAW
    questions: int  # kind QUESTION ("fråga N" in a questions log)
    list_items: int  # status LIST_ITEM
    self_references: int  # status SELF
    external: int  # status EXTERNAL
    counted: int  # references - the five above
    resolved: int  # RESOLVED among the counted
    rate: float  # resolved / counted, with the language model's answers
    rules_rate: float  # the same by the rules alone (CorpusExtraction.rules_rate)
    model_titles: int  # counted and RESOLVED by the model choosing a heading (R4-llm)
    model_topics: int  # given a section by the model after R2 resolved the file (R2-llm)
    by_status: dict[ReferenceStatus, int]
    counted_by_status: dict[ReferenceStatus, int]
    by_kind: tuple[KindTally, ...]
    by_rule: tuple[RuleTally, ...]


class ReportedFinding(_Frozen):
    finding: Finding
    key: str  # Finding.key, for accepted_findings.toml
    file: FileRef | None  # None for a finding about a page or an agreement
    section_heading: str | None  # "7.16 Prismodeller", for a finding about one section
    page_title: str | None  # the title of `Finding.page_url`, when a link of the run has it


class CheckCount(_Frozen):
    check: str
    by_severity: dict[Severity, int]  # findings not accepted
    accepted: int


class FindingSummary(_Frozen):
    """Avvikelser."""

    by_check: tuple[CheckCount, ...]  # every check of step 5, in the order they run
    open: tuple[ReportedFinding, ...]  # not accepted: QUARANTINE first, then REPORT, NOTE


class HeldFile(_Frozen):
    file: FileRef
    findings: tuple[ReportedFinding, ...]  # those that hold it back


class HeldSection(_Frozen):
    file: FileRef
    section: int  # position
    heading: str | None
    findings: tuple[ReportedFinding, ...]


class QuarantineSummary(_Frozen):
    """Karantän: what the index leaves out."""

    files: tuple[HeldFile, ...]
    sections: tuple[HeldSection, ...]
    unchecked: tuple[FileRef, ...]  # parsed files steps 4-5 have not run on


class UnusedAcceptance(_Frozen):
    """An entry of accepted_findings.toml that matched no finding (its reviewer left out)."""

    key: str
    reason: str
    accepted_on: date


class AcceptedSummary(_Frozen):
    """Godkända avvikelser."""

    findings: tuple[ReportedFinding, ...]
    unused: tuple[UnusedAcceptance, ...]


class IngestionReport(_Frozen):
    """Everything the ingestion report says, without its wording."""

    run: RunSummary
    documents: DocumentSummary
    sections: SectionSummary
    register_match: RegisterMatch
    coverage: CoverageSummary
    references: ReferenceSummary
    findings: FindingSummary
    quarantine: QuarantineSummary
    accepted: AcceptedSummary


# --- Building it ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Files:
    """The metadata, sections and pages of the files of the run, to name them in the report."""

    metadata: Mapping[str, DocumentMetadata]
    headings: Mapping[tuple[str, int], str]  # (sha256, section position) -> heading
    pages: Mapping[str, tuple[str, ...]]  # sha256 -> the titles of the pages linking to it
    page_titles: Mapping[str, str]  # page_url -> title

    @classmethod
    def of(cls, result: IngestionResult) -> "_Files":
        pages: defaultdict[str, dict[str, None]] = defaultdict(dict)
        for link in result.links:
            pages[link.sha256][link.page_title] = None
        return cls(
            metadata={e.metadata.sha256: e.metadata for e in result.corpus.extractions},
            headings={
                (chunked.sha256, section.position): section.heading
                for chunked in result.chunked
                for section in chunked.sections
            },
            pages={sha256: tuple(titles) for sha256, titles in pages.items()},
            page_titles={link.page_url: link.page_title for link in result.links},
        )

    def ref(self, sha256: str) -> FileRef:
        metadata = self.metadata.get(sha256)
        pages = self.pages.get(sha256, ())
        if metadata is None:
            return FileRef(sha256=sha256, title=None, pages=pages)
        return FileRef(
            sha256=sha256,
            title=metadata.title,
            document_type=metadata.document_type,
            agreement_number=metadata.agreement_number,
            pages=pages,
        )

    def reported(self, finding: Finding) -> ReportedFinding:
        heading = None
        if finding.sha256 and finding.section is not None:
            heading = self.headings.get((finding.sha256, finding.section))
        return ReportedFinding(
            finding=finding,
            key=finding.key,
            file=self.ref(finding.sha256) if finding.sha256 else None,
            section_heading=heading,
            page_title=self.page_titles.get(finding.page_url) if finding.page_url else None,
        )


def build_report(result: IngestionResult, run: RunInfo) -> IngestionReport:
    """The numbers and lists of the ingestion report for one run."""
    files = _Files.of(result)
    validation = result.validation
    return IngestionReport(
        run=RunSummary(
            started_at=run.started_at,
            finished_at=run.finished_at,
            register_version=run.register_version,
            areas=run.areas,
            model=run.model,
            matching=result.corpus.match_stats,
            accepted_file=str(run.accepted_file),
        ),
        documents=_documents(result, files),
        sections=_sections(result, files),
        register_match=_register_match(result, files),
        coverage=_coverage(validation, run.areas, files),
        references=_references(result.corpus.references, result.corpus.rules_rate),
        findings=_findings(validation.findings, files),
        quarantine=_quarantine(validation, files),
        accepted=AcceptedSummary(
            findings=tuple(files.reported(f) for f in validation.findings if f.accepted_reason),
            unused=tuple(
                UnusedAcceptance(key=entry.key, reason=entry.reason, accepted_on=entry.accepted_on)
                for entry in validation.unused_acceptances
            ),
        ),
    )


def _documents(result: IngestionResult, files: _Files) -> DocumentSummary:
    metadata = [files.metadata[document.sha256] for document in result.parsed]
    types = Counter(item.document_type for item in metadata)
    rules: defaultdict[DocumentType, Counter[str]] = defaultdict(Counter)
    for item in metadata:
        rules[item.document_type][item.type_rule] += 1
    return DocumentSummary(
        files=len(result.parsed),
        by_file_type=dict(Counter(document.file_type for document in result.parsed)),
        pdf_pages=sum(len(document.pages) for document in result.parsed),
        by_type=tuple(
            # The rules by id, fallbacks last: "R11b 1, R14 4, F1 3".
            TypeCount(
                document_type=kind,
                files=types[kind],
                rules=dict(sorted(rules[kind].items(), key=lambda r: (r[0] in _FALLBACKS, r[0]))),
            )
            for kind in DocumentType
            if types[kind]
        ),
        templates=tuple(files.ref(item.sha256) for item in metadata if item.is_template),
        ocr_files=tuple(
            OcrFile(
                file=files.ref(document.sha256),
                pages=tuple(document.pages_needing_ocr),
                page_count=len(document.pages),
            )
            for document in result.parsed
            if document.pages_needing_ocr
        ),
        unlinked=tuple(result.unlinked),
    )


def _sections(result: IngestionResult, files: _Files) -> SectionSummary:
    outlines = Counter(chunked.outline for chunked in result.chunked)
    with_contents = [c for c in result.chunked if c.contents_missing is not None]
    return SectionSummary(
        sections=sum(len(chunked.sections) for chunked in result.chunked),
        chunks=sum(len(chunked.chunks) for chunked in result.chunked),
        by_outline={kind: outlines[kind] for kind in OutlineKind},
        without_sections=tuple(files.ref(c.sha256) for c in result.chunked if not c.sections),
        with_contents=len(with_contents),
        contents_gaps=tuple(
            ContentsGap(file=files.ref(c.sha256), missing=tuple(c.contents_missing))
            for c in with_contents
            if c.contents_missing
        ),
    )


def _register_match(result: IngestionResult, files: _Files) -> RegisterMatch:
    statuses = result.validation.number_status
    counts = Counter(statuses.values())
    numbers: defaultdict[str, dict[str, Severity]] = defaultdict(dict)
    for finding in result.validation.findings:
        if finding.check == procurement_number.CHECK and finding.sha256:
            numbers[finding.sha256][finding.subject] = finding.severity
    return RegisterMatch(
        by_status={status: counts[status] for status in NumberStatus},
        deviating=tuple(
            DeviatingFile(file=files.ref(sha256), numbers=numbers[sha256])
            for sha256, status in statuses.items()
            if status is NumberStatus.DEVIATES
        ),
    )


def _coverage(validation: Validation, areas: Sequence[str], files: _Files) -> CoverageSummary:
    # The run's areas in their order, so an area without agreements in the register
    # still gets its line, then any other area a coverage line names.
    names = list(dict.fromkeys([*areas, *(item.framework_area for item in validation.coverage)]))
    by_area: defaultdict[str, list[AgreementCoverage]] = defaultdict(list)
    for item in validation.coverage:
        by_area[item.framework_area].append(item)
    # The coverage check's groups, which are its findings: by status, then area.
    statuses = list(CoverageStatus)
    groups = sorted(
        coverage.groups(validation.coverage),
        key=lambda group: (
            statuses.index(group.status),
            min(names.index(item.framework_area) for item in group.agreements),
        ),
    )
    return CoverageSummary(
        areas=tuple(_area_coverage(name, by_area[name]) for name in names),
        groups=tuple(_gap_group(group, files) for group in groups),
    )


def _area_coverage(name: str, items: Sequence[AgreementCoverage]) -> AreaCoverage:
    covered = [item for item in items if item.status is CoverageStatus.COVERED]
    statuses = Counter(item.status for item in items)
    return AreaCoverage(
        framework_area=name,
        agreements=len(items),
        covered=len(covered),
        by_card=sum(1 for item in covered if item.cards and not item.main_documents),
        by_main_document=sum(1 for item in covered if item.main_documents and not item.cards),
        by_both=sum(1 for item in covered if item.cards and item.main_documents),
        procurement_version=statuses[CoverageStatus.PROCUREMENT_VERSION],
        held_back=statuses[CoverageStatus.HELD_BACK],
        not_covered=statuses[CoverageStatus.NOT_COVERED],
    )


def _gap_group(group: CoverageGroup, files: _Files) -> GapGroup:
    not_counted = {f.sha256: f for item in group.agreements for f in item.not_counted}
    ordered = [*group.files, *(sha256 for sha256 in not_counted if sha256 not in group.files)]
    return GapGroup(
        status=group.status,
        subject=group.subject,
        framework_areas=tuple(dict.fromkeys(item.framework_area for item in group.agreements)),
        files=tuple(
            NotCountedRef(
                file=files.ref(sha256),
                reason=not_counted[sha256].reason,
                own=not_counted[sha256].own,
            )
            for sha256 in ordered
        ),
        agreements=tuple(
            CoverageGap(
                agreement_number=item.agreement_number,
                procurement_number=item.procurement_number,
                framework_area=item.framework_area,
                supplier_name=item.supplier_name,
            )
            for item in group.agreements
        ),
    )


def _references(references: Sequence[Reference], rules_rate: float) -> ReferenceSummary:
    by_kind: defaultdict[ReferenceKind, list[Reference]] = defaultdict(list)
    by_rule: defaultdict[str | None, list[Reference]] = defaultdict(list)
    for reference in references:
        by_kind[reference.mention.kind].append(reference)
        by_rule[reference.rule].append(reference)
    # Decision 5 of the M4 design: laws and "fråga N" are no references to a section or
    # document of the corpus, a list item is no section ("punkterna 1-6 i detta avsnitt",
    # 997bef854b06 §1.16.3), and a document naming itself is not a link to follow.
    rest = [
        r for r in references if r.mention.kind not in (ReferenceKind.LAW, ReferenceKind.QUESTION)
    ]
    rest_statuses = Counter(r.status for r in rest)
    counted = [r for r in references if counts_in_rate(r)]
    ordered_rules: list[str | None] = [rule for rule in REFERENCE_RULE_NAMES if rule in by_rule]
    ordered_rules += sorted(rule for rule in by_rule if rule and rule not in REFERENCE_RULE_NAMES)
    if None in by_rule:
        ordered_rules.append(None)
    return ReferenceSummary(
        references=len(references),
        laws=len(by_kind[ReferenceKind.LAW]),
        questions=len(by_kind[ReferenceKind.QUESTION]),
        list_items=rest_statuses[ReferenceStatus.LIST_ITEM],
        self_references=rest_statuses[ReferenceStatus.SELF],
        external=rest_statuses[ReferenceStatus.EXTERNAL],
        counted=len(counted),
        resolved=_resolved(counted),
        rate=resolution_rate(references),
        rules_rate=rules_rate,
        model_titles=_resolved(r for r in counted if r.rule == TITLE_RULE),
        model_topics=len(by_rule.get(TOPIC_RULE, [])),
        by_status=_by_status(references),
        counted_by_status=_by_status(counted),
        by_kind=tuple(
            KindTally(kind=kind, **_tally(by_kind[kind])) for kind in ReferenceKind if by_kind[kind]
        ),
        by_rule=tuple(RuleTally(rule=rule, **_tally(by_rule[rule])) for rule in ordered_rules),
    )


def _resolved(references: Iterable[Reference]) -> int:
    return sum(1 for r in references if r.status is ReferenceStatus.RESOLVED)


def _by_status(references: Iterable[Reference]) -> dict[ReferenceStatus, int]:
    counts = Counter(r.status for r in references)
    return {status: counts[status] for status in ReferenceStatus}


def _tally(references: Sequence[Reference]) -> dict[str, Any]:
    """The fields of a `ReferenceTally` for these references."""
    counted = [r for r in references if counts_in_rate(r)]
    statuses = Counter(r.status for r in references)
    return {
        "references": len(references),
        "counted": len(counted),
        "resolved": _resolved(counted),
        "statuses": {status: statuses[status] for status in ReferenceStatus if statuses[status]},
    }


def _check_order(findings: Sequence[Finding]) -> list[str]:
    """Every check of step 5 in the order they run, then any other check a finding names."""
    known = [check.CHECK for check in DOCUMENT_CHECKS] + [coverage.CHECK]
    return list(dict.fromkeys([*known, *(finding.check for finding in findings)]))


def _findings(findings: Sequence[Finding], files: _Files) -> FindingSummary:
    open_findings = [f for f in findings if f.accepted_reason is None]
    counts = Counter((f.check, f.severity) for f in open_findings)
    accepted = Counter(f.check for f in findings if f.accepted_reason is not None)
    order = list(Severity)
    return FindingSummary(
        by_check=tuple(
            CheckCount(
                check=check,
                by_severity={severity: counts[check, severity] for severity in Severity},
                accepted=accepted[check],
            )
            for check in _check_order(findings)
        ),
        open=tuple(
            files.reported(f) for f in sorted(open_findings, key=lambda f: order.index(f.severity))
        ),
    )


def _quarantine(validation: Validation, files: _Files) -> QuarantineSummary:
    held = validation.quarantine
    by_file: defaultdict[str, list[Finding]] = defaultdict(list)
    by_section: defaultdict[tuple[str, int], list[Finding]] = defaultdict(list)
    for finding in validation.findings:
        if not (finding.quarantines and finding.sha256):
            continue
        if finding.section is None:
            by_file[finding.sha256].append(finding)
        else:
            by_section[finding.sha256, finding.section].append(finding)
    # In the order of their first finding; a file held back without one comes last. A file
    # steps 4-5 have not run on is listed apart, whatever an older finding says about it.
    checked = held.files - held.unchecked
    file_order = [sha for sha in by_file if sha in checked]
    file_order += sorted(checked - set(file_order))
    section_order = [pair for pair in by_section if pair in held.sections]
    section_order += sorted(held.sections - set(section_order))
    return QuarantineSummary(
        files=tuple(
            HeldFile(
                file=files.ref(sha256),
                findings=tuple(files.reported(f) for f in by_file.get(sha256, [])),
            )
            for sha256 in file_order
        ),
        sections=tuple(
            HeldSection(
                file=files.ref(sha256),
                section=position,
                heading=files.headings.get((sha256, position)),
                findings=tuple(files.reported(f) for f in by_section.get((sha256, position), [])),
            )
            for sha256, position in section_order
        ),
        unchecked=tuple(files.ref(sha256) for sha256 in sorted(held.unchecked)),
    )


# --- Markdown ------------------------------------------------------------------------------


def render_markdown(report: IngestionReport) -> str:
    """The report for a person, in Swedish."""
    started = report.run.started_at
    lines = [
        f"# Inläsningsrapport {started:%Y-%m-%d %H:%M}",
        "",
        "Rapporten sammanfattar en körning av inläsningens steg 3–5: dokumenten delas i "
        "avsnitt, metadata och hänvisningar läses ut, och allt jämförs med registret över "
        "giltiga ramavtal. En fil anges med sin titel (länktexten på avropa.se) och de "
        "första 12 tecknen i filens hash.",
        "",
    ]
    for part in (
        _md_summary,
        _md_run,
        _md_documents,
        _md_sections,
        _md_register_match,
        _md_coverage,
        _md_references,
        _md_findings,
        _md_quarantine,
        _md_accepted,
    ):
        lines += part(report)
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def _md_summary(report: IngestionReport) -> list[str]:
    refs = report.references
    open_counts = Counter(item.finding.severity for item in report.findings.open)
    held = report.quarantine
    areas = report.coverage.areas
    total = sum(area.agreements for area in areas)
    lines = [
        "## Sammanfattning",
        "",
        f"- {_count(report.documents.files, 'fil', 'filer')} lästes in och gav "
        f"{_n(report.sections.sections)} avsnitt och "
        f"{_count(report.sections.chunks, 'chunk', 'chunkar')}.",
        f"- {_percent(refs.rate)} av hänvisningarna är upplösta "
        f"({_n(refs.resolved)} av {_n(refs.counted)}; bara med regler "
        f"{_percent(refs.rules_rate)}).",
        # The severities as labels ("rapport 62"), so no noun takes a number.
        f"- {_count(len(report.findings.open), 'avvikelse', 'avvikelser')}: "
        + ", ".join(
            f"{SEVERITY_NAMES[severity].lower()} {_n(open_counts[severity])}"
            for severity in Severity
        )
        + f". {_count(len(report.accepted.findings), 'godkänd', 'godkända')}.",
        f"- I karantän: {_count(len(held.files) + len(held.unchecked), 'fil', 'filer')} och "
        f"{_n(len(held.sections))} avsnitt.",
    ]
    if total:
        covered = sum(area.covered for area in areas)
        not_covered = sum(area.not_covered for area in areas)
        lines.append(
            f"- Täckning: {_n(covered)} av {_n(total)} avtal är "
            f"{_word(covered, 'täckt', 'täckta')}; "
            f"{_n(sum(a.procurement_version for a in areas))} har bara upphandlingens version av "
            "huvuddokumentet (den undertecknade publiceras inte på avropa.se), "
            f"{_n(sum(a.held_back for a in areas))} bara dokument i karantän och "
            f"{_n(not_covered)} är {_word(not_covered, 'inte täckt', 'inte täckta')}."
        )
    return lines


def _md_run(report: IngestionReport) -> list[str]:
    run = report.run
    version = run.register_version
    register = "okänd"
    if version:
        dated = str(version.list_date) in version.title
        register = version.title if dated else f"{version.title} ({version.list_date})"
    if run.matching is None:
        model = "kördes inte, så hänvisningarna löstes bara med regler."
    else:
        stats = run.matching
        model = (
            f"{run.model or 'okänd modell'} valde avsnitt där reglerna bara hittade filen: "
            f"{_count(stats.asked, 'fråga', 'frågor')}, "
            f"{_count(stats.answered, 'besvarad', 'besvarade')} med en av rubrikerna. "
            f"{_n(stats.calls)} anrop till modellen och {_n(stats.cache_hits)} svar från cachen."
        )
    return [
        "## Körning",
        "",
        f"- **Start:** {_moment(run.started_at)}",
        f"- **Slut:** {_moment(run.finished_at)}, efter "
        f"{_duration(run.finished_at - run.started_at)}",
        f"- **Registret:** {_md(register)}",
        f"- **Ramavtalsområden:** {_md(', '.join(run.areas)) or 'inga'}",
        f"- **Språkmodell:** {model}",
    ]


def _md_documents(report: IngestionReport) -> list[str]:
    documents = report.documents
    file_types = ", ".join(
        f"{_FILE_TYPE_NAMES.get(kind, kind)} {_n(count)}"
        for kind, count in sorted(documents.by_file_type.items(), key=lambda item: -item[1])
    )
    lines = [
        "## Dokument",
        "",
        f"{_count(documents.files, 'fil', 'filer')} lästes in ({file_types or 'inga'}), med "
        f"sammanlagt {_count(documents.pdf_pages, 'PDF-sida', 'PDF-sidor')}. Dokumenttypen "
        "sätts av den första regeln som passar länken till filen "
        "(ingestion/extract/document_type.py): R01–R14 läser länktexten, filnamnet eller "
        "leverantörskortet, och reservreglerna F1–F3 bara rubriken som länken står under. "
        f"”{NO_RULE}” betyder att ingen regel passade.",
        "",
        "| Dokumenttyp | Del av | Filer | Typregler |",
        "|---|---|---:|---|",
    ]
    for item in documents.by_type:
        rules = ", ".join(
            f"{rule} {_n(count)}" + (" (reservregel)" if rule in FALLBACK_RULES else "")
            for rule, count in item.rules.items()
        )
        group = GROUP_NAMES[DOCUMENT_GROUPS[item.document_type]]
        lines.append(
            f"| {DOCUMENT_TYPE_NAMES[item.document_type]} | {group} | {_n(item.files)} | {rules} |"
        )
    lines.append(f"| **Totalt** | | **{_n(documents.files)}** | |")

    templates = documents.templates
    typed = [ref for ref in templates if ref.document_type is DocumentType.TEMPLATE]
    others = [ref for ref in templates if ref.document_type is not DocumentType.TEMPLATE]
    lines += [
        "",
        f"**Mallar och utkast:** {_count(len(templates), 'fil', 'filer')}. En fil är en mall när "
        "länken säger det (typen Mall) eller när den har ett tomt datumfält (”[DATUM]”) och inte "
        "anger någon avtalsperiod; ett tomt fält för leverantör eller avtalsnummer räcker inte, "
        f"eftersom områdets huvuddokument alltid har det. {_n(len(typed))} har typen Mall"
        + (f" och {_n(len(others))} har ett tomt datumfält:" if others else "."),
    ]
    if others:
        lines.append("")
        for ref in others:  # with the type when the title does not say it, and the pages
            kind = DOCUMENT_TYPE_NAMES[ref.document_type] if ref.document_type else ""
            other = kind.casefold() != (ref.title or "").casefold()
            lines.append(
                f"- {_file(ref)}" + (f", {kind.lower()}" if kind and other else "") + _on_pages(ref)
            )

    lines += [
        "",
        f"**Filer med sidor utan textlager:** {_count(len(documents.ocr_files), 'fil', 'filer')}.",
    ]
    if documents.ocr_files:
        lines += [
            "Texten på de sidorna saknas tills de läses med OCR (ADR 0008).",
            "",
            "| Fil | Sidor utan textlager | Sidor i filen |",
            "|---|---|---:|",
        ]
        lines += [
            f"| {_file(item.file)} | {_pages(item.pages)} | {_n(item.page_count)} |"
            for item in documents.ocr_files
        ]

    unlinked = ", ".join(f"`{sha[:12]}`" for sha in documents.unlinked)
    lines += [
        "",
        "**Filer som ingen sida länkar till längre:** "
        + (f"{unlinked}. De lästes inte in och tas bort ur indexet." if unlinked else "inga."),
    ]
    return lines


def _md_sections(report: IngestionReport) -> list[str]:
    sections = report.sections
    files = sum(sections.by_outline.values())
    lines = [
        "## Avsnitt och chunkar",
        "",
        f"Steg 3 delade {_count(files, 'fil', 'filer')} i {_n(sections.sections)} avsnitt och "
        f"{_count(sections.chunks, 'chunk', 'chunkar')}. Ett avsnitt är ett numrerat avsnitt i "
        "dokumentet (”14.2 Leverantörens uppsägning”); ett långt avsnitt delas i flera chunkar "
        "för sökningen. Dispositionen säger hur filen delades:",
        "",
        "| Disposition | Filer |",
        "|---|---:|",
    ]
    lines += [
        f"| {OUTLINE_NAMES[kind]} | {_n(count)} |" for kind, count in sections.by_outline.items()
    ]
    if sections.without_sections:
        lines += [
            "",
            "**Filer utan avsnitt** (ingen text att dela): "
            + ", ".join(_file(ref) for ref in sections.without_sections)
            + ".",
        ]
    complete = sections.with_contents - len(sections.contents_gaps)
    if sections.with_contents == 1:  # "I 1 av dem" would not do
        which = "Varje nummer i den är ett avsnitt." if complete else "I den saknas:"
    else:
        which = f"I {_n(complete)} av dem är varje nummer i förteckningen ett avsnitt" + (
            "; i de övriga saknas:" if sections.contents_gaps else "."
        )
    lines += [
        "",
        f"**Innehållsförteckning:** {_count(sections.with_contents, 'fil', 'filer')} har en. "
        + which,
    ]
    if sections.contents_gaps:
        lines.append("")
        for gap in sections.contents_gaps:
            shown = ", ".join(gap.missing[:_SHOWN_NUMBERS])
            more = len(gap.missing) - _SHOWN_NUMBERS
            lines.append(
                f"- {_file(gap.file)}: {_md(shown)}" + (f" och {_n(more)} till" if more > 0 else "")
            )
    return lines


def _md_register_match(report: IngestionReport) -> list[str]:
    match = report.register_match
    lines = [
        "## Stämmer med registret",
        "",
        "Varje fils egna diarie- och avtalsnummer jämförs med upphandlingarna på de "
        "avtalssidor som länkar till filen (kontrollen Diarienummer). Det visar om dokumentet "
        "hör till rätt avtal, och hur väl tolkningen läser numren.",
        "",
    ]
    if not sum(match.by_status.values()):
        return [*lines, "Ingen fil jämfördes."]
    lines += ["| Utfall | Filer |", "|---|---:|"]
    lines += [
        f"| {NUMBER_STATUS_NAMES[status]} | {_n(count)} |"
        for status, count in match.by_status.items()
    ]
    if match.deviating:
        lines += ["", "Filer som avviker:", ""]
        for item in match.deviating:
            numbers = ", ".join(
                f"{_md(number)} ({SEVERITY_NAMES[severity].lower()})"
                for number, severity in item.numbers.items()
            )
            lines.append(f"- {_file(item.file)}" + (f": {numbers}" if numbers else ""))
    return lines


def _md_coverage(report: IngestionReport) -> list[str]:
    summary = report.coverage
    lines = [
        "## Täckning",
        "",
        "Varje avtal i registret inom körningens ramavtalsområden ska ha ett inläst "
        "huvuddokument: leverantörens eget ramavtal i leverantörskortet, eller huvuddokumentet "
        "på avtalssidan för avtalets delområde. För en del avtal finns bara upphandlingens "
        "version (en utskrift från TendSign, ”Upphandlingsdokument”) eller en mall: den "
        "undertecknade versionen av de avtalen publiceras inte på avropa.se, så det är "
        "upphandlingens version som indexeras. Ett dokument i karantän indexeras inte.",
        "",
    ]
    if not sum(area.agreements for area in summary.areas):
        return [*lines, "Registret har inga avtal i körningens ramavtalsområden."]
    columns = (
        "agreements",
        "covered",
        "by_card",
        "by_main_document",
        "by_both",
        "procurement_version",
        "held_back",
        "not_covered",
    )
    lines += [
        "| Ramavtalsområde | Avtal | Täckta | via leverantörsavtal | via huvuddokument "
        "| via båda | Bara upphandlingens version | Bara dokument i karantän | Inte täckta |",
        "|---|" + "---:|" * len(columns),
    ]
    for area in summary.areas:
        values = " | ".join(_n(getattr(area, name)) for name in columns)
        lines.append(f"| {_md(area.framework_area)} | {values} |")
    if len(summary.areas) > 1:
        sums = [sum(getattr(area, name) for area in summary.areas) for name in columns]
        lines.append("| **Totalt** | " + " | ".join(f"**{_n(value)}**" for value in sums) + " |")
    if not summary.groups:
        return [*lines, "", "Alla avtal är täckta."]

    # One line per group of the coverage check: all 33 agreements of Bemanningstjänster
    # (23.3-14537-2023) have only 34d71a7e4da0, a TendSign printout.
    lines += [
        "",
        "Avtalen som inte är täckta, i grupper efter dokumenten som täcker dem. Avvikelsen för "
        "varje grupp, med avtalsnumren, står under Avvikelser, Täckning.",
        "",
    ]
    for group in summary.groups:
        status = COVERAGE_STATUS_NAMES[group.status]
        head = (
            f"{status[:1].upper()}{status[1:]}: {_md(', '.join(group.framework_areas))}, "
            f"{_n(len(group.agreements))} avtal"
        )
        about = _md(group.subject)
        if len(group.agreements) == 1:
            about += f", {_md(group.agreements[0].supplier_name)}"
        why = "; ".join(
            f"{_file(item.file)}, {NOT_COUNTED_NAMES[item.reason]}"
            # A supplier's own agreement is named by its number, others by their pages.
            + ("" if item.own else _on_pages(item.file))
            for item in group.files
        )
        lines.append(f"- **{head}** ({about}): {why or 'inget inläst huvuddokument'}.")
    return lines


def _md_references(report: IngestionReport) -> list[str]:
    refs = report.references
    lines = [
        "## Hänvisningar",
        "",
        f"Steg 4 hittade {_count(refs.references, 'hänvisning', 'hänvisningar')} i avsnittens "
        "text (”enligt punkt 6.21”, ”bilaga Priser”, ”Allmänna villkor”) och följde dem till "
        "den fil och det avsnitt de pekar på, bland filerna på samma avtalssidor.",
        "",
        "**Andel upplösta** = upplösta / (alla − lagar och standarder − ”fråga N” − "
        "listpunkter − självhänvisningar − övriga externa)",
        "",
        f"= {_n(refs.resolved)} / ({_n(refs.references)} − {_n(refs.laws)} − "
        f"{_n(refs.questions)} − {_n(refs.list_items)} − {_n(refs.self_references)} − "
        f"{_n(refs.external)}) = {_n(refs.resolved)} / {_n(refs.counted)} = "
        f"**{_percent(refs.rate)}**",
        "",
        "Lagar och ”fråga N” pekar inte på ett dokument på avtalssidan, en listpunkt "
        "(”punkterna 1–6 i detta avsnitt”) är inget avsnitt, och en självhänvisning "
        "(”Huvuddokumentet” i huvuddokumentet) är ingen länk att följa.",
        "",
    ]
    if report.run.matching is None:
        lines.append(
            f"- Språkmodellen kördes inte, så andelen är reglernas: {_percent(refs.rules_rate)}."
        )
    else:
        lines += [
            f"- **Bara med regler:** {_percent(refs.rules_rate)}. Språkmodellen valde rubriken "
            f"för {_count(refs.model_titles, 'hänvisning', 'hänvisningar')} där reglerna bara "
            f"hittade filen ({TITLE_RULE}), och avsnittet för "
            f"{_count(refs.model_topics, 'hänvisning', 'hänvisningar')} till ett dokument med "
            f"ett ämne ({TOPIC_RULE}), som reglerna redan hade löst till filen.",
        ]
    not_published = refs.by_status[ReferenceStatus.NOT_PUBLISHED]
    lines += [
        f"- **Självhänvisningar:** {_n(refs.by_status[ReferenceStatus.SELF])}, räknas inte.",
        f"- **Ej publicerade:** {_n(not_published)}, räknas som "
        f"{_word(not_published, 'ej upplöst', 'ej upplösta')}: målet finns inte "
        "bland filerna på avtalssidan (oftast ett anbudsformulär, eller en fil i ett format som "
        "inte hämtas).",
        "",
        "### Per utfall",
        "",
        "| Utfall | Hänvisningar | I måttet |",
        "|---|---:|---:|",
    ]
    lines += [
        f"| {REFERENCE_STATUS_NAMES[status]} | {_n(count)} | {_n(refs.counted_by_status[status])} |"
        for status, count in refs.by_status.items()
    ]
    lines += [
        "",
        "### Per form",
        "",
        "| Form | Hänvisningar | I måttet | Upplösta | Andel |",
        "|---|---:|---:|---:|---:|",
    ]
    lines += [
        f"| {REFERENCE_KIND_NAMES[tally.kind]} | {_n(tally.references)} | {_n(tally.counted)} "
        f"| {_n(tally.resolved)} | {_share(tally)} |"
        for tally in refs.by_kind
    ]
    lines += [
        "",
        "### Per regel",
        "",
        "Regeln är den som avgjorde utfallet, också när den sökte och inte hittade något "
        "(ingestion/extract/reference_resolver.py).",
        "",
        "| Regel | Vad regeln gör | Hänvisningar | I måttet | Upplösta | Andel | Andra utfall |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for rule_tally in refs.by_rule:
        rule = rule_tally.rule
        name = (REFERENCE_RULE_NAMES.get(rule, "") if rule else _TEXT_RULE) or "–"
        others = ", ".join(
            f"{REFERENCE_STATUS_NAMES[status].lower()} {_n(count)}"
            for status, count in rule_tally.statuses.items()
            if status is not ReferenceStatus.RESOLVED
        )
        lines.append(
            f"| {rule or '–'} | {name} | {_n(rule_tally.references)} | {_n(rule_tally.counted)} "
            f"| {_n(rule_tally.resolved)} | {_share(rule_tally)} | {others or '–'} |"
        )
    return lines


def _md_findings(report: IngestionReport) -> list[str]:
    summary = report.findings
    lines = [
        "## Avvikelser",
        "",
        "Steg 5 jämför det steg 4 hittade med registret och avtalssidorna. **Karantän:** "
        "filen eller avsnittet indexeras inte förrän en person har godkänt avvikelsen. "
        "**Rapport:** en avvikelse att titta på; dokumentet indexeras. **Notering:** värt att "
        "veta, ingen avvikelse.",
        "",
        "| Kontroll | Vad den kontrollerar | Karantän | Rapport | Notering | Godkända |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for count in summary.by_check:
        name, what = CHECK_NAMES.get(count.check, (_md(count.check), ""))
        severities = " | ".join(_n(count.by_severity[severity]) for severity in Severity)
        lines.append(f"| {name} | {what or '–'} | {severities} | {_n(count.accepted)} |")
    if not summary.open:
        return [*lines, "", "Inga avvikelser."]
    checks = [count.check for count in summary.by_check]
    for severity in Severity:
        items = [item for item in summary.open if item.finding.severity is severity]
        if items:
            lines += ["", f"### {SEVERITY_NAMES[severity]} ({_n(len(items))})"]
        for check in checks:
            found = [item for item in items if item.finding.check == check]
            if found:
                lines += ["", f"#### {_check_name(check)} ({_n(len(found))})", ""]
                for item in found:
                    lines += _md_finding(item)
    return lines


def _md_finding(item: ReportedFinding, with_reason: bool = False) -> list[str]:
    """One finding as a list item: what it is about, then its message, evidence and key.

    Listed under its check, it does not name it; among accepted ones (`with_reason`) it does.
    """
    finding = item.finding
    if item.file is not None:
        about = _file(item.file)
        if item.section_heading:
            about += f", avsnitt {_md(item.section_heading)}"
    elif finding.agreement_number:
        about = f"Avtal {_md(finding.agreement_number)}"
    elif finding.page_url:
        # By its title; a page no link of the run is on, by the last part of its address.
        name = item.page_title or finding.page_url.rstrip("/").rsplit("/", 1)[-1]
        about = f"Sidan [{_md(name)}]({finding.page_url})"
    elif finding.check == coverage.CHECK:
        # A group of agreements, named by their procurements: "Avtal i 23.3-14537-2023".
        about = f"Avtal i {_md(finding.subject)}"
    else:
        about = "Hela körningen"
    if with_reason:
        about += f" – {_check_name(finding.check)}"
    subject = _md(finding.subject)
    lines = [
        f"- {about}" + ("" if subject in about else f": {subject}"),
        f"  - {_md(finding.message)}",
    ]
    if finding.evidence:
        lines.append(f"  - Underlag: ”{_md(_cut(finding.evidence))}”")
    if with_reason and finding.accepted_reason:
        lines.append(f"  - Skäl: {_md(finding.accepted_reason)}")
    lines.append(f"  - Nyckel: `{item.key}`")
    return lines


def _md_quarantine(report: IngestionReport) -> list[str]:
    held = report.quarantine
    lines = ["## Karantän", ""]
    if not (held.files or held.sections or held.unchecked):
        return [*lines, "Inget hålls tillbaka."]
    lines += [
        "Filerna och avsnitten nedan indexeras inte. Om en person bedömer att avvikelsen är "
        f"riktig skrivs dess nyckel in i {_accepted_file(report)}, med skäl, granskare och "
        "datum. Vid nästa körning håller avvikelsen då inte tillbaka något.",
        "",
        "```toml",
        "[[accepted]]",
        'key = "<nyckel>"',
        'reason = "<varför avvikelsen är riktig>"',
        'reviewer = "<vem>"',
        f"date = {report.run.started_at.date()}",
        "```",
    ]
    if held.files:
        lines += ["", f"### Filer ({_n(len(held.files))})", ""]
        for item in held.files:
            lines.append(f"- **{_file(item.file)}**")
            lines += _md_reasons(item.findings)
    if held.sections:
        lines += ["", f"### Avsnitt ({_n(len(held.sections))})", ""]
        for section in held.sections:
            heading = _md(section.heading) if section.heading else f"nr {section.section}"
            lines.append(f"- **{_file(section.file)}**, avsnitt {heading}")
            lines += _md_reasons(section.findings)
    if held.unchecked:
        lines += [
            "",
            f"### Ej kontrollerade ({_n(len(held.unchecked))})",
            "",
            "Steg 4 och 5 har inte körts på "
            + (
                "den här filen sedan steg 3 senast sparade den:"
                if len(held.unchecked) == 1
                else "dessa filer sedan steg 3 senast sparade dem:"
            ),
            "",
        ]
        lines += [f"- {_file(ref)}" for ref in held.unchecked]
    return lines


def _md_reasons(findings: Sequence[ReportedFinding]) -> list[str]:
    if not findings:
        return ["  - Ingen avvikelse i den här körningen förklarar det."]
    lines = []
    for item in findings:
        lines.append(
            f"  - {_check_name(item.finding.check)}: {_md(item.finding.message)} "
            f"Nyckel: `{item.key}`"
        )
    return lines


def _md_accepted(report: IngestionReport) -> list[str]:
    accepted = report.accepted
    lines = [
        "## Godkända avvikelser",
        "",
        f"Avvikelser som en person har godkänt i {report.run.accepted_file}, med skälet. De "
        "håller inte tillbaka något.",
        "",
    ]
    if accepted.findings:
        for item in accepted.findings:
            lines += _md_finding(item, with_reason=True)
    else:
        lines.append("Inga.")
    if accepted.unused:
        lines += [
            "",
            f"Godkännanden i {report.run.accepted_file} som inte motsvarar någon avvikelse i den "
            "här körningen (avvikelsen finns inte längre, eller nyckeln är fel):",
            "",
        ]
        lines += [
            f"- `{entry.key}`, godkänd {entry.accepted_on}: {_md(entry.reason)}"
            for entry in accepted.unused
        ]
    return lines


# --- Formatting ----------------------------------------------------------------------------

_FILE_TYPE_NAMES = {"pdf": "PDF", "docx": "Word"}
# Characters that would start markup in running text or a table cell.
_MARKUP = str.maketrans({char: "\\" + char for char in "\\`*_[]<>|"})


def _n(value: int) -> str:
    """A whole number the Swedish way: "13 175" (with a no-break space)."""
    return f"{value:,}".replace(",", _NBSP)


def _percent(share: float) -> str:
    """A share as a percentage the Swedish way: "74,4 %"."""
    return f"{share * 100:.1f}".replace(".", ",") + _NBSP + "%"


def _share(tally: ReferenceTally) -> str:
    return _percent(tally.resolved / tally.counted) if tally.counted else "–"


def _check_name(check: str) -> str:
    return CHECK_NAMES[check][0] if check in CHECK_NAMES else _md(check)


def _word(value: int, one: str, many: str) -> str:
    """The word for a count: singular for one ("fil", "täckt"), else plural."""
    return one if value == 1 else many


def _count(value: int, one: str, many: str) -> str:
    """A count and its noun: "1 fil", "207 filer", "0 filer"."""
    return f"{_n(value)} {_word(value, one, many)}"


def _join(parts: Sequence[str]) -> str:
    """'a', 'a och b', 'a, b och c'."""
    if len(parts) < 2:
        return "".join(parts)
    return f"{', '.join(parts[:-1])} och {parts[-1]}"


def _md(text: str) -> str:
    """Free text for markdown: on one line, with characters that start markup escaped."""
    return " ".join(text.split()).translate(_MARKUP)


def _cut(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= _EVIDENCE_CHARS else text[: _EVIDENCE_CHARS - 1].rstrip() + "…"


def _accepted_file(report: IngestionReport) -> str:
    """'accepted_findings.toml i repots rot', or an absolute path as given."""
    path = report.run.accepted_file
    return path if Path(path).is_absolute() else f"{path} i repots rot"


def _file(ref: FileRef) -> str:
    """'Ramavtal 23.3-2940-20:026 (`14aa1cc8ee3d`)'."""
    title = ref.title or "fil som inte lästes in"
    if ref.agreement_number and ref.agreement_number not in title:
        title += f" {ref.agreement_number}"
    return f"{_md(title)} (`{ref.sha256[:12]}`)"


def _on_pages(ref: FileRef) -> str:
    """', på sidan Licenser och licenstjänster', or '' for a file no link of the run is to."""
    if not ref.pages:
        return ""
    where = "sidan" if len(ref.pages) == 1 else "sidorna"
    return f", på {where} {_md(_join(ref.pages))}"


def _pages(pages: Sequence[int]) -> str:
    """Page numbers with runs of three or more joined: "1, 6–13, 15"."""
    runs: list[list[int]] = []
    for page in sorted(pages):
        if runs and page == runs[-1][-1] + 1:
            runs[-1].append(page)
        else:
            runs.append([page])
    return ", ".join(
        f"{run[0]}–{run[-1]}" if len(run) > 2 else ", ".join(map(str, run)) for run in runs
    )


def _moment(moment: datetime) -> str:
    """'2026-10-06 14:03:12 (UTC+02:00)'."""
    offset = moment.utcoffset() or timedelta()
    minutes = int(offset.total_seconds()) // 60
    zone = "UTC"
    if minutes:
        sign = "+" if minutes > 0 else "−"
        zone += f"{sign}{abs(minutes) // 60:02}:{abs(minutes) % 60:02}"
    return f"{moment:%Y-%m-%d %H:%M:%S} ({zone})"


def _duration(span: timedelta) -> str:
    """'4 min 12 s', '1 h 2 min', '9 s'."""
    total = max(round(span.total_seconds()), 0)
    hours, rest = divmod(total, 3600)
    minutes, seconds = divmod(rest, 60)
    if hours:
        return f"{hours} h {minutes} min"
    if minutes:
        return f"{minutes} min {seconds} s"
    return f"{seconds} s"


# --- JSON and files ------------------------------------------------------------------------


def render_json(report: IngestionReport) -> str:
    """The report's numbers and lists as JSON, with the enum values as stored."""
    return report.model_dump_json(indent=2) + "\n"


def write_report(report: IngestionReport, reports_dir: Path) -> tuple[Path, Path]:
    """Write the report as markdown and JSON, named after the run's start; return both paths."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{report.run.started_at:%Y-%m-%dT%H%M%S}{REPORT_SUFFIX}"
    markdown = reports_dir / f"{stem}.md"
    json = reports_dir / f"{stem}.json"
    markdown.write_text(render_markdown(report), encoding="utf-8")
    json.write_text(render_json(report), encoding="utf-8")
    return markdown, json
