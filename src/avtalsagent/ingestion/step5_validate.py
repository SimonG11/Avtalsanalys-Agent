"""Ingestion step 5: compare what step 4 found with the register and the catalog.

What:
    `validate` runs the checks in `ingestion/checks/`, one module per check,
    and returns their `Finding`s, which files and sections they hold back
    (`Quarantine`), how each register agreement of the run's areas is covered
    by the documents read, and how each file's own numbers compare with its
    pages. `load_accepted` reads the deviations a person has accepted, and
    `apply_accepted` marks the findings they cover.

Why:
    The register is the answer key (architecture plan, section 4): a document
    whose own identity deviates from it is held back with a reason instead of
    being indexed silently. A deviation can be right (a supplier that changed
    its name, a document the site links from the wrong page), so a person can
    accept it in `accepted_findings.toml` at the repository root, with a
    reason, their name and a date. The file is reviewed like code, so every
    acceptance is visible in the history. An accepted finding stays in the
    report, listed apart, and no longer holds anything back.

How:
    Severity (ADR 0009 decision 6): QUARANTINE when the document's own
    identity deviates from the register (its own case number, its own
    agreement number, an organisation number of its parties or tables, a
    supplier card's party slot left unfilled, its stated period), when it has
    no text, or when no page lists it any more,
    and for a section on a scanned page; REPORT for what is not about one
    document's content (coverage gaps, a page whose period differs from the
    register or whose title is no sub-area of it, a document no type rule
    matched); NOTE for what is worth knowing but no deviation (a citation of
    another procurement, a supplier name written differently, a file with
    some scanned pages, a type from a fallback rule, agreements read from the
    procurement's version of their main document). Two stated periods are a
    NOTE, not a QUARANTINE (`checks/agreement_period.py`): an agreement whose
    register start is later than the document's while the end agrees, since
    an agreement signed late starts then; and any period in a document of the
    procurement stage, which states the period as planned before the award.

    The document checks run first, and the acceptances are applied to their
    findings before the quarantine is drawn up. Coverage runs last, on that
    quarantine, since a main document in quarantine does not cover its
    agreements while an accepted one does. A finding is identified
    across runs by `Finding.key` (check, severity, file or page, subject);
    the acceptances are matched on it, so an acceptance applies only to a
    finding of the severity it was written for, and an acceptance that
    matches no finding is reported, so stale entries are noticed.
    `load_accepted` refuses a file it cannot read as intended: a table other
    than `[[accepted]]`, an entry with an unknown field, and an entry without
    a key, reason, reviewer or date, or with a blank reason or reviewer.
"""

import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from avtalsagent.domain.extracted import Finding, Quarantine
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
from avtalsagent.ingestion.checks.context import CheckContext
from avtalsagent.ingestion.checks.coverage import AgreementCoverage
from avtalsagent.ingestion.checks.procurement_number import NumberStatus

# The checks about documents, in the order their findings are listed. Coverage is
# not among them: it runs after them, on their quarantine.
DOCUMENT_CHECKS = (
    procurement_number,
    agreement_number,
    org_numbers,
    supplier_party,
    agreement_period,
    document_type,
    missing_text,
    still_published,
)


# Text that must say something: an empty or blank reason would release a file unexplained.
_NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class AcceptedFinding(BaseModel):
    """A deviation a person has looked at and accepted, from accepted_findings.toml."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str  # `Finding.key`, e.g. "supplier_party:quarantine:7a49e1a6...:556866-4444"
    reason: _NonBlank  # why the deviation is right, in Swedish
    reviewer: _NonBlank  # who accepted it
    accepted_on: date = Field(alias="date")  # when; written `date = 2026-10-07` in the file


@dataclass(frozen=True)
class Validation:
    """What step 5 concluded."""

    findings: list[Finding]  # all of them, accepted ones with `accepted_reason` set
    quarantine: Quarantine
    coverage: list[AgreementCoverage]
    number_status: dict[str, NumberStatus]  # per file (sha256)
    unused_acceptances: list[AcceptedFinding]  # entries that match no finding of this run


def validate(context: CheckContext, accepted: Mapping[str, AcceptedFinding]) -> Validation:
    """Run every check and apply the accepted deviations."""
    found = [finding for check in DOCUMENT_CHECKS for finding in check.run(context)]
    document_findings = apply_accepted(_unique(found), accepted)
    held = quarantine_of(document_findings)
    coverages = coverage.coverage(context, held)
    findings = document_findings + apply_accepted(_unique(coverage.findings(coverages)), accepted)
    keys = {finding.key for finding in findings}
    return Validation(
        findings=findings,
        quarantine=held,
        coverage=coverages,
        number_status={
            file.sha256: procurement_number.number_status(file, context) for file in context.files
        },
        unused_acceptances=[entry for key, entry in accepted.items() if key not in keys],
    )


def load_accepted(path: Path) -> dict[str, AcceptedFinding]:
    """The acceptances in a TOML file of `[[accepted]]` tables; none when the file is missing.

    Raises:
        ValueError: when the file has another table than `[[accepted]]` (a misspelt
            `[[acepted]]` would otherwise accept nothing without a word), or a single
            `[accepted]` table; when two entries have the same key; or when an entry
            lacks a field, has an unknown one, or has a blank reason or reviewer
            (pydantic's ValidationError, a ValueError).
    """
    if not path.is_file():
        return {}
    document = tomllib.loads(path.read_text(encoding="utf-8"))
    if unknown := sorted(set(document) - {"accepted"}):
        raise ValueError(
            f"{path}: unknown table or key {', '.join(map(repr, unknown))}; "
            "each acceptance is an [[accepted]] table"
        )
    entries = document.get("accepted", [])
    if not isinstance(entries, list) or not all(isinstance(raw, dict) for raw in entries):
        raise ValueError(f"{path}: 'accepted' must be [[accepted]] tables, one per acceptance")
    accepted: dict[str, AcceptedFinding] = {}
    for raw in entries:
        entry = AcceptedFinding.model_validate(raw)
        if entry.key in accepted:
            raise ValueError(f"{path}: the key {entry.key!r} is accepted twice")
        accepted[entry.key] = entry
    return accepted


def apply_accepted(
    findings: Sequence[Finding], accepted: Mapping[str, AcceptedFinding]
) -> list[Finding]:
    """The findings, with the reason set on those a person has accepted.

    The key holds the severity, so an acceptance of a NOTE does not apply to a
    QUARANTINE with the same subject in a later run.
    """
    return [
        finding.model_copy(update={"accepted_reason": accepted[finding.key].reason})
        if finding.key in accepted
        else finding
        for finding in findings
    ]


def quarantine_of(findings: Sequence[Finding]) -> Quarantine:
    """The files and sections held back by findings that quarantine and are not accepted."""
    held = [finding for finding in findings if finding.quarantines and finding.sha256]
    return Quarantine(
        files=frozenset(f.sha256 for f in held if f.sha256 and f.section is None),
        sections=frozenset(
            (f.sha256, f.section) for f in held if f.sha256 and f.section is not None
        ),
    )


def _unique(findings: Sequence[Finding]) -> list[Finding]:
    """The findings without repeats, in order: a check can find one deviation twice."""
    return list(dict.fromkeys(findings))
