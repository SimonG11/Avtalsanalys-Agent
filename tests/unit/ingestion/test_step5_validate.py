"""Tests for avtalsagent.ingestion.step5_validate.

The findings are real, from the pilot run of the checks (M4, register of
2026-10-05), with their messages shortened: Microsoft Ireland's organisation
number in the Microsoft main document 171a3cacf5fd (org_numbers, QUARANTINE),
the scanned section 7.16 of e04bad6a0ced (missing_text, QUARANTINE, section 1),
ÅF Digital Solutions AB as the party of the AFRY card 7a49e1a61b31
(supplier_party, QUARANTINE), the supplier name written differently in
b0f5951c99b2 (supplier_party, NOTE) and IBM's 6765/05, whose main document is
not fetched (coverage, REPORT). The acceptances are made up, and so are the
two register editions for the card 14aa1cc8ee3d (23.3-2940-20:026), which
states 2022-12-01 - 2024-11-30 and an extension of at most 24 months: one that
starts the agreement later (NOTE), one that ends it beyond the extension
(QUARANTINE). TestRepositoryFile reads the repository's accepted_findings.toml,
where Simon accepted the first and the third of these findings on 2026-10-07.
"""

import re
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from avtalsagent.domain.extracted import (
    DocumentExtraction,
    DocumentMetadata,
    DocumentType,
    Finding,
    Quarantine,
    Severity,
)
from avtalsagent.ingestion import step5_validate
from avtalsagent.ingestion.checks.context import CheckContext, CheckedFile
from avtalsagent.ingestion.checks.coverage import AgreementCoverage, CoverageStatus
from avtalsagent.ingestion.checks.procurement_number import NumberStatus
from avtalsagent.ingestion.step5_validate import (
    AcceptedFinding,
    apply_accepted,
    load_accepted,
    quarantine_of,
    validate,
)

REPOSITORY = Path(__file__).parents[3]
MICROSOFT_MAIN = "171a3cacf5fd42094d4d46bf49211224476200ca1dae00227d9c55e85df2b9d3"
SCANNED = "e04bad6a0ced579b2cfaf5953c6edd5547acc4e5657cb1097d0d8e189f0a498b"
AFRY_CARD = "7a49e1a61b31d207f4193d1e3b2fcbc7277a7730d090095527c441967f57d1b8"
KNOWIT_CARD = "b0f5951c99b27f31cb8100da2acc96a71137c484c0f760ddec9c3f2482bc859b"
ITK_CARD = "14aa1cc8ee3d99206f9eb6731234934a52958729579bad511f2080f7b4f53611"

MICROSOFT_IRELAND = Finding(
    check="org_numbers",
    severity=Severity.QUARANTINE,
    subject="502052-1307",
    message="Organisationsnumret 502052-1307 (s. 1), som dokumentet anger för Microsoft Ireland "
    "Operations Ltd, tillhör ingen leverantör på upphandlingen på sidan som länkar till "
    "dokumentet (23.5-3718-2024).",
    sha256=MICROSOFT_MAIN,
    evidence="mellan Statens inköpscentral vid Kammarkollegiet, organisationsnummer 202100-0829 "
    "(nedan Kammarkollegiet) och Microsoft Ireland Operations Ltd",
)
SCANNED_SECTION = Finding(
    check="missing_text",
    severity=Severity.QUARANTINE,
    subject="7.16",
    message="Avsnitt 7.16, s. 14-22, omfattar sidorna 15-21 som saknar textlager (ingen OCR "
    "körs), så avsnittets text är ofullständig.",
    sha256=SCANNED,
    section=1,
)
AF_DIGITAL = Finding(
    check="supplier_party",
    severity=Severity.QUARANTINE,
    subject="556866-4444",
    message="Leverantörskortet för avtal 23.3-2940-20:033 har ÅF Digital Solutions AB, "
    "organisationsnummer 556866-4444, som avtalspart, men registret har AFRY Sweden AB, "
    "556224-8012, för avtalet.",
    sha256=AFRY_CARD,
    agreement_number="23.3-2940-20:033",
)
KNOWIT_NAME = Finding(
    check="supplier_party",
    severity=Severity.NOTE,
    subject="Knowit & Precio Fishbone Public IT AB",
    message="Leverantörskortet för avtal 23.3-2940-20:012 skriver avtalsparten Knowit & Precio "
    "Fishbone Public IT AB, men registret har namnet Knowit Public IT AB för samma "
    "organisationsnummer (559309-6794).",
    sha256=KNOWIT_CARD,
    agreement_number="23.3-2940-20:012",
)
LATER_START = Finding(
    check="agreement_period",
    severity=Severity.NOTE,
    subject="2022-12-01 - 2024-11-30",
    message="Dokumentet anger avtalsperioden 2022-12-01 - 2024-11-30, och registret har "
    "2022-12-15 - 2026-11-30 för avtal 23.3-2940-20:026: avtalet började senare än dokumentet "
    "anger.",
    sha256=ITK_CARD,
    agreement_number="23.3-2940-20:026",
)
BEYOND_EXTENSION = LATER_START.model_copy(
    update={
        "severity": Severity.QUARANTINE,
        "message": "Dokumentet anger avtalsperioden 2022-12-01 - 2024-11-30 med förlängning "
        "högst 24 månader, men registret har 2022-12-15 - 2028-11-30 för avtal "
        "23.3-2940-20:026.",
    }
)
IBM_GAP = Finding(
    check="coverage",
    severity=Severity.REPORT,
    subject="6765/05",
    message="Avtal 6765/05 (IBM Svenska AB) täcks inte av något inläst huvuddokument.",
    agreement_number="6765/05",
)


def accepted(finding: Finding, reason: str = "Påhittat skäl för testet.") -> AcceptedFinding:
    return AcceptedFinding.model_validate(
        {"key": finding.key, "reason": reason, "reviewer": "Testare", "date": date(2026, 10, 7)}
    )


# --- accepted_findings.toml -------------------------------------------------------------------


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "accepted_findings.toml"
    path.write_text(text, encoding="utf-8")
    return path


class TestLoadAccepted:
    def test_a_missing_file_accepts_nothing(self, tmp_path: Path) -> None:
        assert load_accepted(tmp_path / "accepted_findings.toml") == {}

    def test_an_entry_is_read_with_its_date(self, tmp_path: Path) -> None:
        path = write(
            tmp_path,
            f"""
[[accepted]]
key = "{AF_DIGITAL.key}"
reason = "Påhittat skäl: en person har granskat avvikelsen."
reviewer = "Testare"
date = 2026-10-07
""",
        )

        entries = load_accepted(path)

        assert list(entries) == [AF_DIGITAL.key]
        entry = entries[AF_DIGITAL.key]
        assert entry.accepted_on == date(2026, 10, 7)
        assert entry.reason == "Påhittat skäl: en person har granskat avvikelsen."
        assert entry.reviewer == "Testare"

    def test_a_key_accepted_twice_is_refused(self, tmp_path: Path) -> None:
        entry = f'[[accepted]]\nkey = "{AF_DIGITAL.key}"\nreason = "x"\nreviewer = "y"\n'
        path = write(tmp_path, f"{entry}date = 2026-10-07\n{entry}date = 2026-10-08\n")

        with pytest.raises(ValueError, match="accepted twice"):
            load_accepted(path)

    def test_an_unknown_field_is_refused(self, tmp_path: Path) -> None:
        # A misspelt field would otherwise be dropped without a word.
        path = write(
            tmp_path,
            f'[[accepted]]\nkey = "{AF_DIGITAL.key}"\nreason = "x"\nreviewer = "y"\n'
            'date = 2026-10-07\nreviewr = "y"\n',
        )

        with pytest.raises(ValidationError, match="reviewr"):
            load_accepted(path)

    def test_an_entry_without_a_reason_is_refused(self, tmp_path: Path) -> None:
        path = write(
            tmp_path, f'[[accepted]]\nkey = "{AF_DIGITAL.key}"\nreviewer = "y"\ndate = 2026-10-07\n'
        )

        with pytest.raises(ValidationError, match="reason"):
            load_accepted(path)

    @pytest.mark.parametrize(
        ("field", "value"), [("reason", ""), ("reason", "  "), ("reviewer", ""), ("reviewer", " ")]
    )
    def test_a_blank_reason_or_reviewer_is_refused(
        self, tmp_path: Path, field: str, value: str
    ) -> None:
        # Otherwise the file is released, and the report lists the finding nowhere.
        entry = {"reason": "Påhittat skäl.", "reviewer": "Testare", field: value}
        path = write(
            tmp_path,
            f'[[accepted]]\nkey = "{AF_DIGITAL.key}"\nreason = "{entry["reason"]}"\n'
            f'reviewer = "{entry["reviewer"]}"\ndate = 2026-10-07\n',
        )

        with pytest.raises(ValidationError, match=field):
            load_accepted(path)

    def test_a_misspelt_table_is_refused(self, tmp_path: Path) -> None:
        # Read as no acceptance at all, it would release nothing and say nothing.
        path = write(
            tmp_path,
            f'[[acepted]]\nkey = "{AF_DIGITAL.key}"\nreason = "x"\nreviewer = "y"\n'
            "date = 2026-10-07\n",
        )

        with pytest.raises(ValueError, match="unknown table or key 'acepted'"):
            load_accepted(path)

    def test_a_single_accepted_table_is_refused(self, tmp_path: Path) -> None:
        path = write(
            tmp_path,
            f'[accepted]\nkey = "{AF_DIGITAL.key}"\nreason = "x"\nreviewer = "y"\n'
            "date = 2026-10-07\n",
        )

        with pytest.raises(ValueError, match=r"'accepted' must be \[\[accepted\]\] tables"):
            load_accepted(path)


class TestRepositoryFile:
    def test_it_releases_the_pilot_findings_simon_accepted(self) -> None:
        # Simon accepted nine quarantine findings of the pilot on 2026-10-07, these two among them.
        entries = load_accepted(REPOSITORY / "accepted_findings.toml")

        found = apply_accepted([MICROSOFT_IRELAND, AF_DIGITAL], entries)

        assert not any(finding.quarantines for finding in found)
        for finding in (MICROSOFT_IRELAND, AF_DIGITAL):
            assert entries[finding.key].reviewer == "Simon (SimonG11)"
            assert entries[finding.key].accepted_on == date(2026, 10, 7)

    def test_every_key_has_the_form_of_a_finding_key(self) -> None:
        # A mistyped key releases nothing; the report would only list it as unused.
        checks = {check.CHECK for check in step5_validate.DOCUMENT_CHECKS} | {"coverage"}
        severities = {severity.value for severity in Severity}

        for key in load_accepted(REPOSITORY / "accepted_findings.toml"):
            check, severity, place_and_subject = key.split(":", 2)
            assert check in checks, key
            assert severity in severities, key
            assert re.fullmatch(r"([0-9a-f]{64}|https://\S+|-):\S.*", place_and_subject), key

    def test_the_example_in_its_header_loads(self, tmp_path: Path) -> None:
        # The commented example shows the format; uncommented, it must be a valid entry.
        lines = (REPOSITORY / "accepted_findings.toml").read_text(encoding="utf-8").splitlines()
        start = lines.index("# [[accepted]]")
        end = lines.index("#", start)
        example = "\n".join(line.removeprefix("# ") for line in lines[start:end])

        [entry] = load_accepted(write(tmp_path, example)).values()

        assert entry.key.startswith("supplier_party:quarantine:")
        assert entry.accepted_on == date(2026, 10, 7)


# --- Accepting and holding back ---------------------------------------------------------------


class TestApplyAccepted:
    def test_an_accepted_finding_gets_its_reason_and_holds_nothing_back(self) -> None:
        reason = "Påhittat skäl: en person har granskat avvikelsen."

        found = apply_accepted(
            [MICROSOFT_IRELAND, AF_DIGITAL], {AF_DIGITAL.key: accepted(AF_DIGITAL, reason)}
        )

        assert found[0] == MICROSOFT_IRELAND
        assert found[1].accepted_reason == reason
        assert not found[1].quarantines
        assert found[1].model_copy(update={"accepted_reason": None}) == AF_DIGITAL

    def test_the_key_names_check_severity_file_and_subject(self) -> None:
        assert AF_DIGITAL.key == f"supplier_party:quarantine:{AFRY_CARD}:556866-4444"
        assert IBM_GAP.key == "coverage:report:-:6765/05"

    def test_an_acceptance_of_another_file_does_not_apply(self) -> None:
        # The key names the file: the same organisation number on another card
        # (ee6107229c37, the other ÅF card) is a deviation of its own.
        other_card = AF_DIGITAL.model_copy(update={"sha256": "ee6107229c37" + "0" * 52})

        [found] = apply_accepted([other_card], {AF_DIGITAL.key: accepted(AF_DIGITAL)})

        assert found.accepted_reason is None


class TestQuarantineOf:
    def test_files_and_sections_held_back(self) -> None:
        held = quarantine_of([MICROSOFT_IRELAND, SCANNED_SECTION])

        assert held.files == {MICROSOFT_MAIN}
        assert held.sections == {(SCANNED, 1)}
        assert held.holds(SCANNED, 1)
        assert not held.holds(SCANNED, 0)

    def test_accepted_notes_and_reports_hold_nothing_back(self) -> None:
        accepted_af = AF_DIGITAL.model_copy(update={"accepted_reason": "Påhittat skäl."})

        held = quarantine_of([accepted_af, KNOWIT_NAME, IBM_GAP])

        assert held == Quarantine(files=frozenset(), sections=frozenset())


# --- validate ---------------------------------------------------------------------------------


def checked_file(sha256: str) -> CheckedFile:
    metadata = DocumentMetadata(
        sha256=sha256,
        title="Ramavtal",
        document_type=DocumentType.SUPPLIER_AGREEMENT,
        type_rule="R01",
        agreement_number="23.3-2940-20:033",
        annex_number=None,
        first_chapter=9,
        tendsign_cover=None,
        is_template=False,
        version_date=None,
        version_rule=None,
        published_on=None,
        site_updated=None,
    )
    return CheckedFile(
        extraction=DocumentExtraction(metadata=metadata, facts=(), mentions=()),
        links=(),
        sections=(),
        file_type="pdf",
        page_count=0,
        ocr_pages=(),
    )


CONTEXT = CheckContext(
    files=(checked_file(AFRY_CARD),),
    links=(),
    register=(),
    areas=("IT-konsulttjänster Resurskonsulter", "Programvaror och tjänster"),
)
IBM_COVERAGE = AgreementCoverage(
    agreement_number="6765/05",
    procurement_number="6765/05",
    framework_area="Programvaror och tjänster",
    supplier_name="IBM Svenska AB",
    status=CoverageStatus.NOT_COVERED,
    cards=(),
    main_documents=(),
    not_counted=(),
)


def check(*findings: Finding) -> SimpleNamespace:
    """A stand-in for a check module: `run(context)` gives these findings."""
    return SimpleNamespace(run=lambda context: list(findings))


class FakeCoverage:
    """A stand-in for checks/coverage.py that remembers the quarantine it was given."""

    def __init__(self) -> None:
        self.quarantine: Quarantine | None = None

    def coverage(self, context: CheckContext, quarantine: Quarantine) -> list[AgreementCoverage]:
        self.quarantine = quarantine
        return [IBM_COVERAGE]

    def findings(self, coverages: Sequence[AgreementCoverage]) -> list[Finding]:
        assert list(coverages) == [IBM_COVERAGE]
        return [IBM_GAP]


@pytest.fixture
def fake_coverage(monkeypatch: pytest.MonkeyPatch) -> FakeCoverage:
    fake = FakeCoverage()
    monkeypatch.setattr(step5_validate, "coverage", fake)
    monkeypatch.setattr(
        step5_validate,
        "DOCUMENT_CHECKS",
        (
            check(MICROSOFT_IRELAND, AF_DIGITAL),
            # A check can find one deviation twice; it is listed once.
            check(SCANNED_SECTION, KNOWIT_NAME, SCANNED_SECTION),
        ),
    )
    return fake


class TestValidate:
    def test_the_findings_of_every_check_then_coverage(self, fake_coverage: FakeCoverage) -> None:
        validation = validate(CONTEXT, {})

        assert validation.findings == [
            MICROSOFT_IRELAND,
            AF_DIGITAL,
            SCANNED_SECTION,
            KNOWIT_NAME,
            IBM_GAP,
        ]
        assert validation.coverage == [IBM_COVERAGE]
        assert validation.number_status == {AFRY_CARD: NumberStatus.NO_NUMBER}

    def test_coverage_sees_what_the_document_checks_hold_back(
        self, fake_coverage: FakeCoverage
    ) -> None:
        # A main document in quarantine does not cover its agreements; an accepted
        # deviation no longer holds its file back.
        validation = validate(CONTEXT, {AF_DIGITAL.key: accepted(AF_DIGITAL)})

        assert fake_coverage.quarantine == validation.quarantine
        assert validation.quarantine.files == {MICROSOFT_MAIN}
        assert validation.quarantine.sections == {(SCANNED, 1)}

    def test_acceptances_apply_to_coverage_findings_too(self, fake_coverage: FakeCoverage) -> None:
        reason = "Påhittat skäl: huvuddokumentet är en .doc-fil som steg 1 inte hämtar."

        validation = validate(CONTEXT, {IBM_GAP.key: accepted(IBM_GAP, reason)})

        assert validation.findings[-1].accepted_reason == reason

    def test_an_accepted_note_does_not_release_the_file_once_it_quarantines(
        self, fake_coverage: FakeCoverage, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Run 1: the register starts the agreement later (NOTE), and a person accepts it.
        acceptance = accepted(LATER_START, "Påhittat skäl: avtalet undertecknades 2022-12-15.")
        monkeypatch.setattr(step5_validate, "DOCUMENT_CHECKS", (check(LATER_START),))
        first = validate(CONTEXT, {acceptance.key: acceptance})
        assert first.findings[0].accepted_reason == acceptance.reason
        # Run 2: the next register edition ends it beyond the extension, a QUARANTINE about
        # the same stated period. The acceptance was not written for that.
        monkeypatch.setattr(step5_validate, "DOCUMENT_CHECKS", (check(BEYOND_EXTENSION),))

        second = validate(CONTEXT, {acceptance.key: acceptance})

        assert second.findings[0] == BEYOND_EXTENSION
        assert second.quarantine.files == {ITK_CARD}
        assert second.unused_acceptances == [acceptance]

    def test_an_acceptance_that_matches_no_finding_is_reported(
        self, fake_coverage: FakeCoverage
    ) -> None:
        # The other ÅF card's finding, accepted under this card's hash by mistake.
        stale = accepted(AF_DIGITAL.model_copy(update={"sha256": "ee6107229c37" + "0" * 52}))
        used = accepted(AF_DIGITAL)

        validation = validate(CONTEXT, {stale.key: stale, used.key: used})

        assert validation.unused_acceptances == [stale]
