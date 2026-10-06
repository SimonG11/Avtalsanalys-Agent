"""Database tables, defined with SQLAlchemy 2.

What:
    The register tables built from the Excel master list (M1): list version,
    procurement, supplier, supplier name, agreement, sub-area and the link
    between agreements and sub-areas. The document tables (M2): the agreement
    pages on avropa.se, the files downloaded from them and which page links to
    which file. The section tables (M3): each parsed file, its numbered
    sections and the chunks they are cut into for search. The extraction
    tables (M4): what step 4 found in each file (metadata, facts, references
    and their targets) and the findings of step 5.

Why:
    The Excel list has one row per supplier and sub-area. Splitting it into
    these tables gives each fact one place: the supplier lives on the
    agreement, dates on the agreement's sub-area (they differ per sub-area in
    the real list), the sub-area hierarchy in `sub_area`, and a supplier is
    identified by its organisation number, never by its name (one org number
    can have several names, e.g. hotels in the same company).

How:
    Each class is one table. Alembic migrations in `db/migrations/` create
    the tables; `register/load.py`, `ingestion/catalog.py`,
    `ingestion/section_store.py` and `ingestion/extraction_store.py` fill
    them. The register tables mirror the most recently loaded list;
    `register_version` records every load.
"""

from datetime import date, datetime

from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class RegisterVersion(Base):
    """One load of the master list, identified by the date in its title row."""

    __tablename__ = "register_version"

    list_date: Mapped[date] = mapped_column(Date, primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    loaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    row_count: Mapped[int] = mapped_column(Integer)
    rejected_count: Mapped[int] = mapped_column(Integer)


class Procurement(Base):
    """A procurement (upphandling), identified by its case number (diarienummer)."""

    __tablename__ = "procurement"

    # One procurement can span several framework areas (23.3-2965-20 does), so
    # the framework area is not stored here; it is reached through sub_area.
    procurement_number: Mapped[str] = mapped_column(String(32), primary_key=True)


class Supplier(Base):
    """A supplier, identified by its normalised organisation number.

    Swedish numbers are NNNNNN-NNNN; foreign ones are kept as written
    (see `domain.identifiers.normalize_org_number`).
    """

    __tablename__ = "supplier"

    org_number: Mapped[str] = mapped_column(String(20), primary_key=True)


class SupplierName(Base):
    """A name a supplier appears under; one org number can have several."""

    __tablename__ = "supplier_name"

    org_number: Mapped[str] = mapped_column(ForeignKey("supplier.org_number"), primary_key=True)
    name: Mapped[str] = mapped_column(Text, primary_key=True)
    former_name: Mapped[str | None] = mapped_column(Text)


class Agreement(Base):
    """A framework agreement between one supplier and Statens inköpscentral."""

    __tablename__ = "agreement"

    agreement_number: Mapped[str] = mapped_column(String(40), primary_key=True)
    procurement_number: Mapped[str] = mapped_column(
        ForeignKey("procurement.procurement_number"), index=True
    )
    sequence: Mapped[str | None] = mapped_column(String(8))
    org_number: Mapped[str] = mapped_column(ForeignKey("supplier.org_number"), index=True)
    supplier_name: Mapped[str] = mapped_column(Text)  # the name on this agreement's rows


class SubArea(Base):
    """One level of a sub-area path, e.g. "Gävle" under "Gävleborgs län"."""

    __tablename__ = "sub_area"
    __table_args__ = (UniqueConstraint("framework_area", "path"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    framework_area: Mapped[str] = mapped_column(Text)
    path: Mapped[str] = mapped_column(Text)  # full path, levels joined with " / "
    name: Mapped[str] = mapped_column(Text)  # the last level of the path
    level: Mapped[int] = mapped_column(Integer)  # 1 = top level
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("sub_area.id"))


class AgreementSubArea(Base):
    """Which sub-areas an agreement covers, and when (one Excel row = one link)."""

    __tablename__ = "agreement_sub_area"

    agreement_number: Mapped[str] = mapped_column(
        ForeignKey("agreement.agreement_number"), primary_key=True
    )
    sub_area_id: Mapped[int] = mapped_column(ForeignKey("sub_area.id"), primary_key=True)
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date] = mapped_column(Date)
    max_extension_to: Mapped[date | None] = mapped_column(Date)


class AgreementPage(Base):
    """A framework-agreement page on avropa.se, as last read."""

    __tablename__ = "agreement_page"

    url: Mapped[str] = mapped_column(Text, primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    # From "Ramavtalsnummer" and the supplier cards; same numbers as procurement.
    procurement_numbers: Mapped[list[str]] = mapped_column(ARRAY(String(32)))
    agreement_period: Mapped[str | None] = mapped_column(Text)  # as written on the page
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # The first fetch whose index on avropa.se no longer listed the page; None while it is
    # listed. Step 5 holds back a file when every page that links to it is missing.
    missing_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SourceDocument(Base):
    """A file downloaded from avropa.se, stored on disk under its SHA-256 hash."""

    __tablename__ = "source_document"

    url: Mapped[str] = mapped_column(Text, primary_key=True)  # without "?v=..."
    version: Mapped[str | None] = mapped_column(String(32))  # the "?v=..." value
    etag: Mapped[str | None] = mapped_column(Text)  # for links without a version
    file_type: Mapped[str] = mapped_column(String(8))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    local_path: Mapped[str] = mapped_column(Text)  # relative to the data directory
    downloaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AgreementPageDocument(Base):
    """A link from a page to a document, with how the page lists it."""

    __tablename__ = "agreement_page_document"

    page_url: Mapped[str] = mapped_column(ForeignKey("agreement_page.url"), primary_key=True)
    document_url: Mapped[str] = mapped_column(ForeignKey("source_document.url"), primary_key=True)
    title: Mapped[str] = mapped_column(Text)  # the link text
    category: Mapped[str | None] = mapped_column(Text)  # e.g. "Avtal"
    # Set for a document in a supplier's card, e.g. "23.3-5834-2022-018".
    agreement_number: Mapped[str | None] = mapped_column(String(40), index=True)
    site_updated: Mapped[date | None] = mapped_column(Date)  # "Senast uppdaterad"


class ParsedFile(Base):
    """A downloaded file after steps 2 and 3: how it was read and how it was split."""

    __tablename__ = "parsed_file"

    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)  # as in source_document
    file_type: Mapped[str] = mapped_column(String(8))
    parser: Mapped[str] = mapped_column(Text)  # parser and version, e.g. "docling 2.133.0 (...)"
    page_count: Mapped[int] = mapped_column(Integer)  # 0 for Word files
    # Scanned pages without a text layer; their text is missing until OCR (M3b).
    pages_needing_ocr: Mapped[list[int]] = mapped_column(ARRAY(Integer))
    # How the sections were found (OutlineKind): numbered, questions, headings or none.
    outline: Mapped[str] = mapped_column(String(16))
    section_count: Mapped[int] = mapped_column(Integer)
    chunk_count: Mapped[int] = mapped_column(Integer)
    chunked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DocumentSection(Base):
    """A numbered section of a file, or the text before its first heading."""

    __tablename__ = "document_section"

    sha256: Mapped[str] = mapped_column(
        ForeignKey("parsed_file.sha256", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[int] = mapped_column(Integer, primary_key=True)  # 0-based order
    number: Mapped[str | None] = mapped_column(String(32))  # e.g. "6.21.9"
    title: Mapped[str] = mapped_column(Text)
    level: Mapped[int] = mapped_column(Integer)  # 0 for the text before the first heading
    parent_position: Mapped[int | None] = mapped_column(Integer)
    path: Mapped[list[str]] = mapped_column(ARRAY(Text))  # headings from the top down
    page_start: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)


class SectionChunk(Base):
    """A piece of a section, the unit that is searched (parent-child)."""

    __tablename__ = "section_chunk"
    __table_args__ = (
        ForeignKeyConstraint(
            ["sha256", "section_position"],
            ["document_section.sha256", "document_section.position"],
            ondelete="CASCADE",
        ),
    )

    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    section_position: Mapped[int] = mapped_column(Integer, primary_key=True)
    position: Mapped[int] = mapped_column(Integer, primary_key=True)  # order within the section
    context_header: Mapped[str] = mapped_column(Text)  # "ramavtal › dokument › rubrikstig"
    text: Mapped[str] = mapped_column(Text)


class DocumentMetadata(Base):
    """What step 4 found out about a file as a whole (`domain.extracted.DocumentMetadata`)."""

    __tablename__ = "document_metadata"

    sha256: Mapped[str] = mapped_column(
        ForeignKey("parsed_file.sha256", ondelete="CASCADE"), primary_key=True
    )
    title: Mapped[str] = mapped_column(Text)  # the link text used most often
    document_type: Mapped[str] = mapped_column(String(32))  # DocumentType, e.g. "general_terms"
    type_rule: Mapped[str] = mapped_column(String(32))  # e.g. "R07"; "none" when no rule matched
    # Both follow from document_type and are stored for queries: the group (agreement,
    # procurement or support) and whether the document is part of the agreement or the
    # procurement rather than only support.
    document_group: Mapped[str] = mapped_column(String(16))
    binding: Mapped[bool] = mapped_column(Boolean)
    # Set for a file in a supplier's card, from the link.
    agreement_number: Mapped[str | None] = mapped_column(String(40), index=True)
    annex_number: Mapped[str | None] = mapped_column(String(32))  # "4.1"
    first_chapter: Mapped[int | None] = mapped_column(Integer)  # "6" for a printed chapter 6
    tendsign_cover: Mapped[str | None] = mapped_column(String(32))  # "Upphandlingsdokument"
    is_template: Mapped[bool] = mapped_column(Boolean)
    version_date: Mapped[date | None] = mapped_column(Date)
    version_rule: Mapped[str | None] = mapped_column(String(32))  # e.g. "file_name"
    published_on: Mapped[date | None] = mapped_column(Date)
    site_updated: Mapped[date | None] = mapped_column(Date)  # latest "Senast uppdaterad"


class DocumentFact(Base):
    """One thing a file states, with the block it states it in (`domain.extracted.Fact`)."""

    __tablename__ = "document_fact"

    # Each run numbers the rows 1, 2, ... and replaces all of them, so an id is not
    # stable across runs.
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    sha256: Mapped[str] = mapped_column(
        ForeignKey("parsed_file.sha256", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(32))  # FactKind, e.g. "org_number"
    # Normalised: "23.3-2940-20" (in the register's spelling when the register has the
    # number), "556866-4444", "2024-11-14", "48"; for a placeholder, its kind ("date").
    value: Mapped[str] = mapped_column(Text)
    raw: Mapped[str] = mapped_column(Text)  # the text it was read from, as written
    rule: Mapped[str] = mapped_column(String(32))  # e.g. "P3" (ingestion/extract/)
    # Facts are also read from page headers and footers, which belong to no section,
    # so a fact points at its block in ParsedDocument.blocks, not at a section.
    block_index: Mapped[int] = mapped_column(Integer)
    page: Mapped[int | None] = mapped_column(Integer)  # 1-based; None for Word files
    role: Mapped[str | None] = mapped_column(String(16))  # FactRole: "self" or "citation"
    name: Mapped[str | None] = mapped_column(Text)  # a party's name as written
    scope: Mapped[str | None] = mapped_column(Text)  # the sub-area of a period: "AO3"
    statement: Mapped[int | None] = mapped_column(Integer)  # shared by a start and its end


class DocumentReference(Base):
    """A reference in a section's text and what it resolved to (`domain.extracted.Reference`)."""

    __tablename__ = "document_reference"
    __table_args__ = (
        ForeignKeyConstraint(
            ["sha256", "section_position"],
            ["document_section.sha256", "document_section.position"],
            ondelete="CASCADE",
        ),
        # Without it, deleting the sections of a run scans this table once per section.
        Index("ix_document_reference_section", "sha256", "section_position"),
    )

    # Numbered 1, 2, ... by each run, like document_fact; reference_target points to it.
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    sha256: Mapped[str] = mapped_column(String(64))  # the file the reference is in
    section_position: Mapped[int] = mapped_column(Integer)
    char_start: Mapped[int] = mapped_column(Integer)  # offsets in document_section.text
    char_end: Mapped[int] = mapped_column(Integer)
    raw: Mapped[str] = mapped_column(Text)  # e.g. "punkt 6.21.9"
    kind: Mapped[str] = mapped_column(String(32))  # ReferenceKind, e.g. "section_number"
    target_key: Mapped[str] = mapped_column(Text)  # the number, title or name pointed to
    document_name: Mapped[str | None] = mapped_column(Text)  # "p. 6.19.7 i Allmänna villkor"
    topic: Mapped[str | None] = mapped_column(Text)  # "Allmänna villkor gällande viten"
    replaces: Mapped[bool] = mapped_column(Boolean)  # "ersätter avsnitt 7.19.1.3"
    # Two steps, two rules and two statuses. Finding the mention: the pattern (never
    # empty) and the status the text alone decides (law, placeholder, list item).
    # Resolving it: the status, and the rule that resolved it (empty when unresolved).
    pattern_rule: Mapped[str] = mapped_column(String(32))  # e.g. "R1"
    mention_status: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32))  # ReferenceStatus, e.g. "resolved"
    resolve_rule: Mapped[str | None] = mapped_column(String(32))  # e.g. "R4-llm"


class ReferenceTarget(Base):
    """A file, or a section of it, that a reference points to.

    One per page when the reference is resolved, one per candidate when it is
    ambiguous, and the file that was searched when a number or title is missing.
    """

    __tablename__ = "reference_target"
    __table_args__ = (
        # MATCH SIMPLE, Postgres' default: a target without a section (the whole
        # document) is checked only against parsed_file, through target_sha256.
        ForeignKeyConstraint(
            ["target_sha256", "target_section_position"],
            ["document_section.sha256", "document_section.position"],
            ondelete="CASCADE",
        ),
        Index("ix_reference_target_section", "target_sha256", "target_section_position"),
    )

    reference_id: Mapped[int] = mapped_column(
        ForeignKey("document_reference.id", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[int] = mapped_column(Integer, primary_key=True)  # order in the targets
    target_sha256: Mapped[str] = mapped_column(ForeignKey("parsed_file.sha256", ondelete="CASCADE"))
    target_section_position: Mapped[int | None] = mapped_column(Integer)
    # The agreement page through which the target was found; None when every page
    # that links to the referring file gives the same target.
    page_url: Mapped[str | None] = mapped_column(Text)


class ValidationFinding(Base):
    """A result of a step-5 check (`domain.extracted.Finding`)."""

    __tablename__ = "validation_finding"
    __table_args__ = (
        # A quarantine holds back a file or a section of it, so it must name the file;
        # without this a finding could quarantine nothing and fail open.
        CheckConstraint(
            "severity <> 'quarantine' OR sha256 IS NOT NULL", name="quarantine_names_a_file"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    check_name: Mapped[str] = mapped_column(String(32))  # e.g. "org_numbers" (ingestion/checks/)
    severity: Mapped[str] = mapped_column(String(16))  # Severity: quarantine, report or note
    subject: Mapped[str] = mapped_column(Text)  # what deviates, e.g. "556866-4444"
    message: Mapped[str] = mapped_column(Text)  # in Swedish, as in the ingestion report
    # Not a foreign key: a finding can be about a file that has no parsed_file row (its
    # parse failed), and a cascade from parsed_file would delete findings unseen.
    sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    section_position: Mapped[int | None] = mapped_column(Integer)
    agreement_number: Mapped[str | None] = mapped_column(String(40))
    page_url: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[str | None] = mapped_column(Text)  # the text the finding rests on
    # Set when a person has accepted the deviation in accepted_findings.toml; an
    # accepted finding does not quarantine.
    accepted_reason: Mapped[str | None] = mapped_column(Text)
