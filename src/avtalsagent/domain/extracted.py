"""What ingestion steps 4 and 5 find in a document and conclude about it.

What:
    Step 4 (`ingestion/step4_extract.py`) gives each file a `DocumentMetadata`
    (what kind of document it is and when it was written), a list of `Fact`s
    (case and agreement numbers, organisation numbers, the parties, the
    agreement period) and the `ReferenceMention`s in its sections ("punkt
    6.21.9", "bilaga Priser"). Resolving a mention against the other files of
    the same agreement page gives a `Reference`. Step 5
    (`ingestion/step5_validate.py`) compares the facts with the register and
    the catalog and gives `Finding`s. A finding with severity QUARANTINE keeps
    the document, or one of its sections, out of the index (M5).

Why:
    The register is the answer key (architecture plan, section 4): a document
    must agree with it on its procurement, its supplier and its dates, and one
    that does not is held back with a reason instead of being indexed
    silently. Plain models keep both steps testable without a database. Every
    fact, reference and finding keeps the text and the rule it came from, so
    a reviewer can check each one against the document by hand.

How:
    Pydantic models without behaviour beyond small helpers. The values of the
    enums are stored in the database as they are; the report gives them
    Swedish names.
"""

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

# Statens inköpscentral vid Kammarkollegiet, the contracting authority. It is a
# party to every framework agreement and is not a supplier in the register.
CONTRACTING_AUTHORITY_ORG_NUMBER = "202100-0829"


class DocumentType(StrEnum):
    """What a document is. The Swedish names are those of the architecture plan (3.2)."""

    SUPPLIER_AGREEMENT = "supplier_agreement"  # a supplier's signed "Ramavtal" (supplier card)
    MAIN_DOCUMENT = "main_document"  # "Ramavtalets huvuddokument", "Volymavtalets huvudavtal"
    GENERAL_TERMS = "general_terms"  # "Allmänna villkor"
    ANNEX = "annex"  # other annexes: "Avropsrutin", "NUTS 2 indelning", Microsoft "Bilaga 1"
    PRICE_ANNEX = "price_annex"  # "Prisbilaga", "Timpriser"
    REQUIREMENTS_CATALOGUE = "requirements_catalogue"  # "Kravkatalog"
    REQUIREMENTS_SPECIFICATION = "requirements_specification"  # "Kravspecifikation"
    AMENDMENT = "amendment"  # "Tilläggsavtal", "Tillägg och förtydliganden"
    LICENCE_TERMS = "licence_terms"  # Microsoft's and IBM's own terms
    PROCUREMENT_DOCUMENT = "procurement_document"  # "Upphandlingsdokument", "Anbudsinbjudan"
    QUESTIONS_AND_ANSWERS = "questions_and_answers"  # "Frågor och svar"
    CALL_OFF_GUIDANCE = "call_off_guidance"  # "Vägledning", "Snabbguide", "Checklista"
    REQUIREMENTS_REPORT = "requirements_report"  # "Redovisning av hållbarhetskrav"
    TEMPLATE = "template"  # "Avropsmall", "Utkast till personuppgiftsbiträdesavtal"


class DocumentGroup(StrEnum):
    AGREEMENT = "agreement"  # the agreement and its annexes (avtal)
    PROCUREMENT = "procurement"  # the procurement documents and their questions (upphandling)
    SUPPORT = "support"  # support for call-offs (stöd); not part of the agreement


DOCUMENT_GROUPS: dict[DocumentType, DocumentGroup] = {
    DocumentType.SUPPLIER_AGREEMENT: DocumentGroup.AGREEMENT,
    DocumentType.MAIN_DOCUMENT: DocumentGroup.AGREEMENT,
    DocumentType.GENERAL_TERMS: DocumentGroup.AGREEMENT,
    DocumentType.ANNEX: DocumentGroup.AGREEMENT,
    DocumentType.PRICE_ANNEX: DocumentGroup.AGREEMENT,
    DocumentType.REQUIREMENTS_CATALOGUE: DocumentGroup.AGREEMENT,
    DocumentType.REQUIREMENTS_SPECIFICATION: DocumentGroup.AGREEMENT,
    DocumentType.AMENDMENT: DocumentGroup.AGREEMENT,
    DocumentType.LICENCE_TERMS: DocumentGroup.AGREEMENT,
    DocumentType.PROCUREMENT_DOCUMENT: DocumentGroup.PROCUREMENT,
    DocumentType.QUESTIONS_AND_ANSWERS: DocumentGroup.PROCUREMENT,
    DocumentType.CALL_OFF_GUIDANCE: DocumentGroup.SUPPORT,
    DocumentType.REQUIREMENTS_REPORT: DocumentGroup.SUPPORT,
    DocumentType.TEMPLATE: DocumentGroup.SUPPORT,
}


class DocumentMetadata(BaseModel):
    """What step 4 found out about a file as a whole."""

    model_config = ConfigDict(frozen=True)

    sha256: str
    title: str  # the link text used most often, e.g. "Allmänna villkor"
    document_type: DocumentType
    type_rule: str  # the rule that gave the type, e.g. "R05" (ingestion/extract/document_type.py)
    agreement_number: str | None  # set for a file in a supplier's card, from the link
    annex_number: str | None  # "4.1" from "Bilaga 4.1 Microsoft Business ...", or a file name
    # The number of the first top-level section when it is not 1: a chapter printed
    # from a larger procurement document keeps that document's numbering ("6 Allmänna villkor").
    first_chapter: int | None
    # The cover word of a document printed from TendSign, the procurement system:
    # "Upphandlingsdokument", "Ramavtal" or "Inbjudan". A main document with the cover
    # "Upphandlingsdokument" is the version from the procurement, not the signed one.
    tendsign_cover: str | None
    is_template: bool  # has unfilled fields such as "[DATUM]" or "XXXXXX-XXXX"
    version_date: date | None  # when the document was written or last changed, if it says so
    version_rule: str | None  # where version_date comes from, e.g. "file_name"
    site_updated: date | None  # the latest "Senast uppdaterad" of its links on avropa.se

    @property
    def group(self) -> DocumentGroup:
        return DOCUMENT_GROUPS[self.document_type]

    @property
    def binding(self) -> bool:
        """Whether the document is part of the agreement or the procurement, not only support."""
        return self.group is not DocumentGroup.SUPPORT


class FactKind(StrEnum):
    PROCUREMENT_NUMBER = "procurement_number"  # value: `ProcurementNumber.key`, "23.3-5890-2023"
    AGREEMENT_NUMBER = "agreement_number"  # value: `AgreementReference.key`
    ORG_NUMBER = "org_number"  # a Swedish organisation number whose check digit is right
    PARTY = "party"  # a party named in "mellan X, organisationsnummer N, nedan ..."
    PERIOD_START = "period_start"  # the agreement period (ISO date)
    PERIOD_END = "period_end"
    PERIOD_MONTHS = "period_months"  # "löper under en period av 48 månader"
    EXTENSION_MONTHS = "extension_months"  # the longest extension; "0" for "Ingen förlängning"
    PLANNED_START = "planned_start"  # "beräknas träda i kraft": a plan, never checked
    SIGNED_ON = "signed_on"  # the date of an electronic signature
    PLACEHOLDER = "placeholder"  # an unfilled field of a template; value says which kind


class FactRole(StrEnum):
    """For case and agreement numbers: whose number it is."""

    SELF = "self"  # the document's own number: page header, cover, "Ramavtal med avtalsnummer X"
    CITATION = "citation"  # the number of another agreement, mentioned in the text


class Fact(BaseModel):
    """One thing a document states, with where it states it."""

    model_config = ConfigDict(frozen=True)

    kind: FactKind
    value: str  # normalised: "23.3-2940-2020", "556866-4444", "2024-11-14", "48"
    raw: str  # the text it was read from, as written
    rule: str  # the rule that found it, e.g. "P3" (see the modules in ingestion/extract/)
    block: int  # index in ParsedDocument.blocks
    page: int | None  # 1-based; None for Word files
    # Position of the section the block ended up in; None for blocks that step 3
    # removes (page headers and footers, the table of contents).
    section: int | None = None
    role: FactRole | None = None  # set for case and agreement numbers
    name: str | None = None  # set for a party: the name as written


class ReferenceKind(StrEnum):
    SECTION_NUMBER = "section_number"  # "punkt 6.21.9", "enligt 10.4"
    SECTION_TITLE = "section_title"  # "avsnitt Avtalsbrott och påföljder"
    DOCUMENT = "document"  # "Allmänna villkor", "Kravkatalogen"
    ANNEX_NUMBER = "annex_number"  # "bilaga 3"
    ANNEX_NAME = "annex_name"  # "bilaga Priser"
    QUESTION = "question"  # "fråga 12", in a questions-and-answers log
    LAW = "law"  # "17 kap. 17 § LOU", "art. 32 GDPR": counted, never resolved to a file


class ReferenceStatus(StrEnum):
    RESOLVED = "resolved"  # the target is a file (and section) in the corpus
    SELF = "self"  # the document refers to itself ("Huvuddokumentet" in the main document)
    LIST_ITEM = "list_item"  # the number is an item of a list ("punkterna 1-6"), not a section
    # The target is not among the fetched files of the agreement page: a tender form that
    # was never published, or a file type step 1 does not fetch (.xlsx, .doc).
    NOT_PUBLISHED = "not_published"
    AMBIGUOUS = "ambiguous"  # more than one candidate; the targets list them
    NUMBER_MISSING = "number_missing"  # no section with that number in the target document
    TITLE_MISSING = "title_missing"  # no section with that title
    PLACEHOLDER = "placeholder"  # a template field: "Bilaga nr x", "avsnitt X.1.1"
    EXTERNAL = "external"  # a law, regulation or standard


class ReferenceMention(BaseModel):
    """A reference as found in a section's text, before it is resolved."""

    model_config = ConfigDict(frozen=True)

    section: int  # position of the section the text is in
    start: int  # character offsets in Section.text
    end: int
    raw: str  # the matched text, e.g. "punkt 6.21.9"
    kind: ReferenceKind
    key: str  # the number, title, annex number or document name the text points to
    rule: str  # the pattern that found it, e.g. "R1"
    document_name: str | None = None  # the document named with it: "p. 6.19.7 i Allmänna villkor"
    # In a sentence that replaces or removes the target ("ersätter avsnitt 7.19.1.3").
    replaces: bool = False
    # Set when the text alone decides the outcome (LAW, PLACEHOLDER, LIST_ITEM).
    status: ReferenceStatus | None = None


class ReferenceTarget(BaseModel):
    model_config = ConfigDict(frozen=True)

    sha256: str
    section: int | None  # None when the reference is to the whole document
    # The agreement page through which the target was found. A template linked from
    # several pages can point to a different file on each page.
    page_url: str | None


class Reference(BaseModel):
    """A mention after resolution."""

    model_config = ConfigDict(frozen=True)

    sha256: str  # the file the mention is in
    mention: ReferenceMention
    status: ReferenceStatus
    rule: str | None  # the rule that resolved it, e.g. "R4" or "R4-llm"; None if unresolved
    targets: tuple[ReferenceTarget, ...] = ()  # one per page, or the candidates when AMBIGUOUS


class Severity(StrEnum):
    QUARANTINE = "quarantine"  # kept out of the index until a person explains the deviation
    REPORT = "report"  # a deviation for a person to look at; the document is indexed
    NOTE = "note"  # worth knowing, e.g. a supplier name written differently


class Finding(BaseModel):
    """A result of a step-5 check."""

    model_config = ConfigDict(frozen=True)

    check: str  # the check that made it, e.g. "procurement_number" (ingestion/checks/)
    severity: Severity
    subject: str  # what deviates, e.g. "556866-4444"
    message: str  # in Swedish, for the ingestion report
    sha256: str | None = None  # the file it is about, if any
    section: int | None = None  # the section, when only one section is affected
    agreement_number: str | None = None
    page_url: str | None = None
    evidence: str | None = None  # the text the finding rests on
    # Set when a person has accepted the deviation in accepted_findings.toml.
    accepted_reason: str | None = None

    @property
    def key(self) -> str:
        """Identifies the finding across runs, e.g. "party_org:7a49e1a61b31...:556866-4444"."""
        return f"{self.check}:{self.sha256 or '-'}:{self.subject}"

    @property
    def quarantines(self) -> bool:
        return self.severity is Severity.QUARANTINE and self.accepted_reason is None


class DocumentExtraction(BaseModel):
    """Everything step 4 found in one file."""

    model_config = ConfigDict(frozen=True)

    metadata: DocumentMetadata
    facts: tuple[Fact, ...]
    mentions: tuple[ReferenceMention, ...]
