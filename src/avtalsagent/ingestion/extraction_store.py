"""The database side of steps 4 and 5: what they compare with, and what they found.

What:
    `catalog_links`, `register_entries` and `register_version` read the
    catalog and the register, which steps 4 and 5 compare the documents with.
    `save_extraction` replaces the stored metadata, facts, references and
    reference targets; `save_findings` replaces the findings. `quarantine`
    returns the files and sections the index (M5) must leave out.

Why:
    The extraction modules and the checks work on plain values so they can be
    tested without a database. This module is the only place that reads their
    inputs from Postgres and writes their results, as `section_store.py` is
    for steps 2 and 3. Keeping a document out of the index is the point of
    step 5, so `quarantine` fails closed: a file that has not been through
    steps 4 and 5 is held back too.

How:
    SQLAlchemy statements on the tables in `db/models.py`. Each run replaces
    all rows, so files that are gone disappear. The pipeline calls
    `save_sections`, `save_extraction` and `save_findings` in one transaction,
    so the stored facts, references and findings always belong to the stored
    sections. `save_sections` alone deletes every parsed_file row, which
    cascades to the extraction tables; the files it inserts again then have no
    document_metadata row, and `quarantine` counts each of them as unchecked
    until steps 4 and 5 have run. Findings have no foreign key and are not
    cascaded, so a file whose parse failed can still have one.
"""

from collections.abc import Sequence
from typing import Any

from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from avtalsagent.db import models
from avtalsagent.domain.documents import CatalogLink
from avtalsagent.domain.extracted import (
    DocumentExtraction,
    DocumentMetadata,
    Fact,
    Finding,
    Quarantine,
    Reference,
    Severity,
)
from avtalsagent.domain.register import RegisterEntry, RegisterVersion
from avtalsagent.register.load import SUB_AREA_SEPARATOR


def catalog_links(session: Session) -> list[CatalogLink]:
    """Every stored link from an agreement page to a downloaded file, with the file's hash."""
    rows = session.execute(
        select(models.AgreementPageDocument, models.AgreementPage, models.SourceDocument.sha256)
        .join(
            models.AgreementPage, models.AgreementPage.url == models.AgreementPageDocument.page_url
        )
        .join(
            models.SourceDocument,
            models.SourceDocument.url == models.AgreementPageDocument.document_url,
        )
        .order_by(models.AgreementPageDocument.page_url, models.AgreementPageDocument.document_url)
    )
    return [
        CatalogLink(
            sha256=sha256,
            url=link.document_url,
            title=link.title,
            category=link.category,
            agreement_number=link.agreement_number,
            site_updated=link.site_updated,
            page_url=page.url,
            page_title=page.title,
            page_procurement_numbers=tuple(page.procurement_numbers),
            page_period=page.agreement_period,
            page_missing_since=page.missing_since,
        )
        for link, page, sha256 in rows
    ]


def register_entries(session: Session) -> list[RegisterEntry]:
    """One entry per agreement and sub-area of the loaded register.

    The register tables keep one row per agreement and sub-area (a repeated
    Excel row is stored once), with the supplier name of the agreement's first
    row; the former name is the one stored with that name. In the 2026-10-05
    list 4 agreements have rows with another spelling ("Consid" and "Consid
    AB" on 23.3-1688-2024-013); their entries all carry the first.
    """
    rows = session.execute(
        select(
            models.Agreement,
            models.SupplierName.former_name,
            models.SubArea,
            models.AgreementSubArea,
        )
        .join(
            models.AgreementSubArea,
            models.AgreementSubArea.agreement_number == models.Agreement.agreement_number,
        )
        .join(models.SubArea, models.SubArea.id == models.AgreementSubArea.sub_area_id)
        .outerjoin(
            models.SupplierName,
            (models.SupplierName.org_number == models.Agreement.org_number)
            & (models.SupplierName.name == models.Agreement.supplier_name),
        )
        .order_by(
            models.Agreement.agreement_number, models.SubArea.framework_area, models.SubArea.path
        )
    )
    return [
        RegisterEntry(
            agreement_number=agreement.agreement_number,
            procurement_number=agreement.procurement_number,
            org_number=agreement.org_number,
            supplier_name=agreement.supplier_name,
            former_supplier_name=former_name,
            framework_area=sub_area.framework_area,
            sub_area_path=tuple(sub_area.path.split(SUB_AREA_SEPARATOR)),
            valid_from=link.valid_from,
            valid_to=link.valid_to,
            max_extension_to=link.max_extension_to,
        )
        for agreement, former_name, sub_area, link in rows
    ]


def register_version(session: Session) -> RegisterVersion | None:
    """The edition of the register loaded last, which the tables hold; None before any load."""
    row = session.scalars(
        select(models.RegisterVersion).order_by(
            models.RegisterVersion.loaded_at.desc(), models.RegisterVersion.list_date.desc()
        )
    ).first()
    return None if row is None else RegisterVersion(list_date=row.list_date, title=row.title)


def save_extraction(
    session: Session, extractions: Sequence[DocumentExtraction], references: Sequence[Reference]
) -> None:
    """Replace all stored metadata, facts, references and reference targets with this run's."""
    for model in (
        models.ReferenceTarget,
        models.DocumentReference,
        models.DocumentFact,
        models.DocumentMetadata,
    ):
        session.execute(delete(model))

    metadata_rows = [_metadata_row(extraction.metadata) for extraction in extractions]
    facts = [
        (extraction.metadata.sha256, fact)
        for extraction in extractions
        for fact in extraction.facts
    ]
    fact_rows = [
        _fact_row(number, sha256, fact) for number, (sha256, fact) in enumerate(facts, start=1)
    ]
    reference_rows: list[dict[str, Any]] = []
    target_rows: list[dict[str, Any]] = []
    for number, reference in enumerate(references, start=1):
        reference_rows.append(_reference_row(number, reference))
        target_rows.extend(
            {
                "reference_id": number,
                "position": position,
                "target_sha256": target.sha256,
                "target_section_position": target.section,
                "page_url": target.page_url,
            }
            for position, target in enumerate(reference.targets)
        )

    # Parents before children. A list of rows is sent in batches (executemany);
    # one statement with all rows would pass Postgres' limit of 65,535 parameters.
    for model, rows in (
        (models.DocumentMetadata, metadata_rows),
        (models.DocumentFact, fact_rows),
        (models.DocumentReference, reference_rows),
        (models.ReferenceTarget, target_rows),
    ):
        if rows:
            session.execute(insert(model), rows)


def save_findings(session: Session, findings: Sequence[Finding]) -> None:
    """Replace all stored findings with this run's, accepted ones included."""
    session.execute(delete(models.ValidationFinding))
    rows = [
        {
            "id": number,
            "check_name": finding.check,
            "severity": finding.severity.value,
            "subject": finding.subject,
            "message": finding.message,
            "sha256": finding.sha256,
            "section_position": finding.section,
            "agreement_number": finding.agreement_number,
            "page_url": finding.page_url,
            "evidence": finding.evidence,
            "accepted_reason": finding.accepted_reason,
        }
        for number, finding in enumerate(findings, start=1)
    ]
    if rows:
        session.execute(insert(models.ValidationFinding), rows)


def quarantine(session: Session) -> Quarantine:
    """The files and sections held back by a finding that is not accepted, and unchecked files."""
    files: set[str] = set()
    sections: set[tuple[str, int]] = set()
    held = session.execute(
        select(models.ValidationFinding.sha256, models.ValidationFinding.section_position).where(
            models.ValidationFinding.severity == Severity.QUARANTINE.value,
            models.ValidationFinding.accepted_reason.is_(None),
        )
    )
    for sha256, section in held:
        if sha256 is None:  # never: the table's check constraint requires the file
            continue
        if section is None:
            files.add(sha256)
        else:
            sections.add((sha256, section))

    unchecked = set(
        session.scalars(
            select(models.ParsedFile.sha256)
            .outerjoin(
                models.DocumentMetadata,
                models.DocumentMetadata.sha256 == models.ParsedFile.sha256,
            )
            .where(models.DocumentMetadata.sha256.is_(None))
        )
    )
    return Quarantine(
        files=frozenset(files | unchecked),
        sections=frozenset(sections),
        unchecked=frozenset(unchecked),
    )


def _metadata_row(metadata: DocumentMetadata) -> dict[str, Any]:
    return {
        "sha256": metadata.sha256,
        "title": metadata.title,
        "document_type": metadata.document_type.value,
        "type_rule": metadata.type_rule,
        "document_group": metadata.group.value,
        "binding": metadata.binding,
        "agreement_number": metadata.agreement_number,
        "annex_number": metadata.annex_number,
        "first_chapter": metadata.first_chapter,
        "tendsign_cover": metadata.tendsign_cover,
        "is_template": metadata.is_template,
        "version_date": metadata.version_date,
        "version_rule": metadata.version_rule,
        "published_on": metadata.published_on,
        "site_updated": metadata.site_updated,
    }


def _fact_row(number: int, sha256: str, fact: Fact) -> dict[str, Any]:
    return {
        "id": number,
        "sha256": sha256,
        "kind": fact.kind.value,
        "value": fact.value,
        "raw": fact.raw,
        "rule": fact.rule,
        "block_index": fact.block,
        "page": fact.page,
        "role": None if fact.role is None else fact.role.value,
        "name": fact.name,
        "scope": fact.scope,
        "statement": fact.statement,
    }


def _reference_row(number: int, reference: Reference) -> dict[str, Any]:
    mention = reference.mention
    return {
        "id": number,
        "sha256": reference.sha256,
        "section_position": mention.section,
        "char_start": mention.start,
        "char_end": mention.end,
        "raw": mention.raw,
        "kind": mention.kind.value,
        "target_key": mention.key,
        "document_name": mention.document_name,
        "topic": mention.topic,
        "replaces": mention.replaces,
        "pattern_rule": mention.rule,
        "mention_status": None if mention.status is None else mention.status.value,
        "status": reference.status.value,
        "resolve_rule": reference.rule,
    }
