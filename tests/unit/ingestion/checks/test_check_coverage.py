"""Tests for avtalsagent.ingestion.checks.coverage.

The files, links, pages and register rows are real, from the pilot: the supplier
cards ee6107229c37 ("Ramavtal" of 23.3-2940-20:018,
ÅF/AFRY) and 264aff0ce61a (23.3-2940-20:010, Chas) on the page
"IT-konsulttjänster 2. Ledning av IT-projekt", whose area main document
cbe12fd30683 is a TendSign printout: p1 "Upphandlingsdokument" "2021-02-02"; the
main documents 0692da436391 and e31f81c753c7 of the two pages of 23.3-1688-2024,
both with the cover "Ramavtal"; 65d611d12eab, the IT-säkerhet main document with
"[DATUM (dag-mån-år)]" on p7, in quarantine in the pilot (agreement_number);
171a3cacf5fd, the Microsoft volume agreement's main document; IBM's "IBM
Användningsvillkor-Allmänna villkor" (255e496fa266) on the page "Volymavtal för
IBM", whose main document is a .doc file step 1 does not fetch. The hashes are
the files' real ones. Made up, and said so where used: the printouts of the
23.3-1688-2024 pages, a printout linked from pages of two procurements, and a
renamed page.
"""

from datetime import date

from avtalsagent.domain.documents import CatalogLink
from avtalsagent.domain.extracted import (
    DocumentExtraction,
    DocumentMetadata,
    DocumentType,
    Finding,
    Quarantine,
    Severity,
)
from avtalsagent.domain.register import RegisterEntry
from avtalsagent.ingestion.checks.context import CheckContext, CheckedFile
from avtalsagent.ingestion.checks.coverage import (
    AgreementCoverage,
    CoverageStatus,
    NotCounted,
    NotCountedFile,
    coverage,
    findings,
    groups,
    not_counted_reason,
)

ITK = "IT-konsulttjänster Resurskonsulter"
SOFTWARE = "Programvaror och tjänster"
ITK2 = "IT-konsulttjänster 2. Ledning av IT-projekt"
ITK1 = "IT-konsulttjänster 1. Verksamhetens IT-behov"
ITK3 = "IT-konsulttjänster 3. IT-säkerhet"
ITK5 = "IT-konsulttjänster 5. IT-konsultlösningar"

CARD_018 = "ee6107229c37264e5184d5e7dcac266cea9b7a0acbf6032de305964f5eea3d89"
CARD_010 = "264aff0ce61a308e9a194719495c9a9de36510b029544d4beba39a639f9c42eb"
MAIN_ITK2020 = "cbe12fd306835fdffb793e602d2c64c0898ea51facad121f5876c3f0597dc656"
MAIN_ITK1 = "0692da436391917c01ad4fe2bc651d7d4faa3ea2ff400ce6960d2e3206306164"
MAIN_ITK5 = "e31f81c753c7a733ae1f893cdc1e3b9d41d2ecf3737ef3feb6304202ac47adda"
MAIN_ITK3 = "65d611d12eabd54c4d481a023d0f1aa932f18f1e34f97e7989b4d228d57623ef"
MAIN_MICROSOFT = "171a3cacf5fd42094d4d46bf49211224476200ca1dae00227d9c55e85df2b9d3"
IBM_TERMS = "255e496fa2667bed29e507958ddbc2d912513025dbff6b918d555563574a43aa"


def entry(
    agreement: str, procurement: str, supplier: str, sub_area: tuple[str, ...], area: str = ITK
) -> RegisterEntry:
    return RegisterEntry(
        agreement_number=agreement,
        procurement_number=procurement,
        org_number="556224-8012",
        supplier_name=supplier,
        former_supplier_name=None,
        framework_area=area,
        sub_area_path=(area, *sub_area),
        valid_from=date(2022, 12, 1),
        valid_to=date(2026, 11, 30),
        max_extension_to=None,
    )


RENEWED = "Förnyad konkurrensutsättning"
AFRY_018 = entry("23.3-2940-20:018", "23.3-2940-20", "AFRY Sweden AB", (ITK2, RENEWED))
CHAS_010 = entry("23.3-2940-20:010", "23.3-2940-20", "Chas Visual Management AB", (ITK2, RENEWED))
CASTRA = entry("23.3-1688-2024-001", "23.3-1688-2024", "Castra Group AB", (ITK1, RENEWED))
CAPGEMINI = entry("23.3-1688-2024-010", "23.3-1688-2024", "Capgemini Sverige AB", (ITK5, RENEWED))
IBM = entry("6765/05", "6765/05", "IBM Svenska AB", ("Volymavtal för IBM",), SOFTWARE)
MICROSOFT = entry(
    "23.5-3718-2024", "23.5-3718-2024", "Microsoft AB", ("Volymavtal för Microsoft",), SOFTWARE
)
IT_DRIFT = ("IT-drift Mindre, upp till 200 anställda",)
ADVANIA = entry("23.3-5890-2023-003", "23.3-5890-2023", "Advania Sverige AB", IT_DRIFT, "IT-drift")
REGISTER = (AFRY_018, CHAS_010, CASTRA, CAPGEMINI, IBM, MICROSOFT, ADVANIA)
AREAS = (ITK, SOFTWARE)  # IT-drift is not in this run

# Two more agreements of each 23.3-1688-2024 sub-area, and IT-säkerhet, for the groups.
CGI_002 = entry("23.3-1688-2024-002", "23.3-1688-2024", "CGI Sverige AB", (ITK1, RENEWED))
CGI_011 = entry("23.3-1688-2024-011", "23.3-1688-2024", "CGI Sverige AB", (ITK5, RENEWED))
CASTRA_ITK3 = entry("23.3-8321-2024-001", "23.3-8321-2024", "Castra Group AB", (ITK3,))
CHAS_ITK3 = entry("23.3-8321-2024-002", "23.3-8321-2024", "Chas Visual Management AB", (ITK3,))


def checked(
    sha256: str,
    document_type: DocumentType,
    page_title: str,
    procurement: str,
    card: str | None = None,
    cover: str | None = "Ramavtal",
    template: bool = False,
) -> CheckedFile:
    title = {
        DocumentType.SUPPLIER_AGREEMENT: "Ramavtal",
        DocumentType.MAIN_DOCUMENT: "Ramavtalets huvuddokument",
    }.get(document_type, "IBM Användningsvillkor-Allmänna villkor")
    metadata = DocumentMetadata(
        sha256=sha256,
        title=title,
        document_type=document_type,
        type_rule="R01",
        agreement_number=card,
        annex_number=None,
        first_chapter=None,
        tendsign_cover=cover,
        is_template=template,
        version_date=None,
        version_rule=None,
        published_on=None,
        site_updated=None,
    )
    link = CatalogLink(
        sha256=sha256,
        url=f"https://www.avropa.se/globalassets/{sha256[:12]}.pdf",
        title=title,
        category=None if card else "Avtal",
        agreement_number=card,
        site_updated=None,
        page_url=f"https://www.avropa.se/ramavtal/{page_title}",
        page_title=page_title,
        page_procurement_numbers=(procurement,),
        page_period=None,
    )
    extraction = DocumentExtraction(metadata=metadata, facts=(), mentions=())
    return CheckedFile(extraction, (link,), (), "pdf", 1, ())


def card(sha256: str, number: str) -> CheckedFile:
    return checked(sha256, DocumentType.SUPPLIER_AGREEMENT, ITK2, "23.3-2940-20", card=number)


def main(
    sha256: str,
    page_title: str,
    procurement: str,
    cover: str | None = "Ramavtal",
    template: bool = False,
) -> CheckedFile:
    return checked(
        sha256, DocumentType.MAIN_DOCUMENT, page_title, procurement, cover=cover, template=template
    )


PRINTOUT_ITK2020 = main(MAIN_ITK2020, ITK2, "23.3-2940-20", cover="Upphandlingsdokument")
# 65d611d12eab has no cover; its "[DATUM (dag-mån-år)]" makes it a template.
TEMPLATE_ITK3 = main(MAIN_ITK3, ITK3, "23.3-8321-2024", cover=None, template=True)
MICROSOFT_MAIN = main(MAIN_MICROSOFT, "Volymavtal för Microsoft", "23.5-3718-2024", cover=None)
NO_QUARANTINE = Quarantine(files=frozenset(), sections=frozenset())


def run(
    *files: CheckedFile,
    quarantine: Quarantine = NO_QUARANTINE,
    register: tuple[RegisterEntry, ...] = REGISTER,
) -> list[AgreementCoverage]:
    context = CheckContext(
        files=files,
        links=tuple(link for file in files for link in file.links),
        register=register,
        areas=AREAS,
    )
    return coverage(context, quarantine)


def of(coverages: list[AgreementCoverage], number: str) -> AgreementCoverage:
    return next(item for item in coverages if item.agreement_number == number)


def held(*shas: str) -> Quarantine:
    return Quarantine(files=frozenset(shas), sections=frozenset())


def printout(sha256: str) -> NotCountedFile:
    return NotCountedFile(sha256, NotCounted.TENDSIGN_PRINTOUT, own=False)


# --- coverage: which files cover an agreement -----------------------------------------------


def test_one_line_per_agreement_of_the_runs_areas_in_register_order() -> None:
    numbers = [item.agreement_number for item in run()]

    # 23.3-5890-2023-003 is IT-drift, an area this run did not fetch.
    assert numbers == [
        "23.3-2940-20:018",
        "23.3-2940-20:010",
        "23.3-1688-2024-001",
        "23.3-1688-2024-010",
        "6765/05",
        "23.5-3718-2024",
    ]


def test_a_supplier_card_covers_its_own_agreement_only() -> None:
    coverages = run(card(CARD_018, "23.3-2940-20:018"))

    assert of(coverages, "23.3-2940-20:018") == AgreementCoverage(
        agreement_number="23.3-2940-20:018",
        procurement_number="23.3-2940-20",
        framework_area=ITK,
        supplier_name="AFRY Sweden AB",
        status=CoverageStatus.COVERED,
        cards=(CARD_018,),
        main_documents=(),
        not_counted=(),
    )
    assert of(coverages, "23.3-2940-20:010").status is CoverageStatus.NOT_COVERED


def test_a_card_number_is_compared_by_key() -> None:
    # The cover of ee6107229c37 writes "23.3.2940-20:018"; a link written so is the same.
    coverages = run(card(CARD_018, "23.3.2940-20:018"))

    assert of(coverages, "23.3-2940-20:018").cards == (CARD_018,)


def test_an_area_main_document_covers_the_agreements_of_its_pages_sub_area() -> None:
    # 23.3-1688-2024 has a page and a main document per sub-area.
    coverages = run(
        main(MAIN_ITK1, ITK1, "23.3-1688-2024"), main(MAIN_ITK5, ITK5, "23.3-1688-2024")
    )

    assert of(coverages, "23.3-1688-2024-001").main_documents == (MAIN_ITK1,)
    assert of(coverages, "23.3-1688-2024-010").main_documents == (MAIN_ITK5,)


def test_a_main_document_on_another_sub_areas_page_does_not_cover() -> None:
    coverages = run(main(MAIN_ITK5, ITK5, "23.3-1688-2024"))

    assert of(coverages, "23.3-1688-2024-001").status is CoverageStatus.NOT_COVERED


def test_a_main_document_on_a_page_that_is_no_sub_area_covers_nothing() -> None:
    # Made up: the page of sub-area 5 renamed "5. IT-konsultlösningar". Taken as the whole
    # procurement, its main document would cover sub-area 1's agreements too.
    coverages = run(main(MAIN_ITK5, "5. IT-konsultlösningar", "23.3-1688-2024"))

    assert of(coverages, "23.3-1688-2024-001").status is CoverageStatus.NOT_COVERED
    assert of(coverages, "23.3-1688-2024-010").status is CoverageStatus.NOT_COVERED


def test_other_document_types_on_the_page_do_not_cover() -> None:
    terms = checked(IBM_TERMS, DocumentType.LICENCE_TERMS, "Volymavtal för IBM", "6765/05")

    item = of(run(terms), "6765/05")
    assert (item.status, item.not_counted) == (CoverageStatus.NOT_COVERED, ())


# --- coverage: which files count, and the status --------------------------------------------


def test_a_procurement_stage_printout_covers_only_as_the_procurements_version() -> None:
    item = of(run(PRINTOUT_ITK2020), "23.3-2940-20:010")

    assert (item.status, item.main_documents, item.not_counted) == (
        CoverageStatus.PROCUREMENT_VERSION,
        (),
        (printout(MAIN_ITK2020),),
    )
    assert (item.procurement_versions, item.held_back) == ((MAIN_ITK2020,), ())


def test_a_template_covers_only_as_the_procurements_version() -> None:
    template = main(MAIN_ITK1, ITK1, "23.3-1688-2024", cover=None, template=True)

    item = of(run(template), "23.3-1688-2024-001")
    assert (item.status, item.not_counted) == (
        CoverageStatus.PROCUREMENT_VERSION,
        (NotCountedFile(MAIN_ITK1, NotCounted.TEMPLATE, own=False),),
    )


def test_a_quarantined_main_document_does_not_count() -> None:
    assert of(run(MICROSOFT_MAIN), "23.5-3718-2024").status is CoverageStatus.COVERED

    item = of(run(MICROSOFT_MAIN, quarantine=held(MAIN_MICROSOFT)), "23.5-3718-2024")
    assert (item.status, item.held_back, item.procurement_versions) == (
        CoverageStatus.HELD_BACK,
        (MAIN_MICROSOFT,),
        (),
    )


def test_a_quarantined_template_is_held_back_not_the_procurements_version() -> None:
    # 65d611d12eab is both; in quarantine it is not in the index at all.
    register = (*REGISTER, CASTRA_ITK3)

    item = of(
        run(TEMPLATE_ITK3, quarantine=held(MAIN_ITK3), register=register),
        CASTRA_ITK3.agreement_number,
    )

    assert (item.status, item.not_counted) == (
        CoverageStatus.HELD_BACK,
        (NotCountedFile(MAIN_ITK3, NotCounted.QUARANTINED, own=False),),
    )


def test_a_quarantined_section_does_not_stop_a_file_from_counting() -> None:
    quarantine = Quarantine(files=frozenset(), sections=frozenset({(MAIN_ITK1, 3)}))

    coverages = run(main(MAIN_ITK1, ITK1, "23.3-1688-2024"), quarantine=quarantine)

    assert of(coverages, "23.3-1688-2024-001").status is CoverageStatus.COVERED


def test_the_procurements_version_comes_before_a_quarantined_own_card() -> None:
    # The pilot's ÅF and Tieto cards: their own card is in quarantine (supplier_party),
    # and the ITK 2020 main document, the procurement's version, is read.
    coverages = run(
        card(CARD_018, "23.3-2940-20:018"),
        card(CARD_010, "23.3-2940-20:010"),
        PRINTOUT_ITK2020,
        quarantine=held(CARD_018),
    )

    afry = of(coverages, "23.3-2940-20:018")
    assert (afry.status, afry.cards, afry.not_counted) == (
        CoverageStatus.PROCUREMENT_VERSION,
        (),
        (NotCountedFile(CARD_018, NotCounted.QUARANTINED, own=True), printout(MAIN_ITK2020)),
    )
    # A counted own card comes before the procurement's version, which is still listed.
    chas = of(coverages, "23.3-2940-20:010")
    assert (chas.status, chas.cards, chas.not_counted) == (
        CoverageStatus.COVERED,
        (CARD_010,),
        (printout(MAIN_ITK2020),),
    )


def test_the_reason_a_main_document_does_not_count() -> None:
    printout_metadata = PRINTOUT_ITK2020.metadata.model_copy(update={"is_template": True})

    assert not_counted_reason(TEMPLATE_ITK3.metadata, held(MAIN_ITK3)) is NotCounted.QUARANTINED
    # A printout is also a draft ("[DATUM]"); being the procurement's version says more.
    assert not_counted_reason(printout_metadata, NO_QUARANTINE) is NotCounted.TENDSIGN_PRINTOUT
    assert not_counted_reason(TEMPLATE_ITK3.metadata, NO_QUARANTINE) is NotCounted.TEMPLATE
    assert not_counted_reason(MICROSOFT_MAIN.metadata, NO_QUARANTINE) is None


# --- groups and findings --------------------------------------------------------------------

PILOT_LIKE = (
    card(CARD_018, "23.3-2940-20:018"),
    PRINTOUT_ITK2020,
    main(MAIN_ITK1, ITK1, "23.3-1688-2024"),
    main(MAIN_ITK5, ITK5, "23.3-1688-2024"),
    MICROSOFT_MAIN,
)


def test_a_group_per_status_and_files_and_one_per_agreement_not_covered() -> None:
    # Two agreements of 23.3-2940-20 read from the printout, one with its card held back.
    register = (*REGISTER, CASTRA_ITK3, CHAS_ITK3)
    files = (*PILOT_LIKE, TEMPLATE_ITK3)
    quarantine = held(CARD_018, MAIN_ITK3, MAIN_MICROSOFT)
    coverages = run(*files, quarantine=quarantine, register=register)
    # IBM's 6765/05 and a made-up second agreement without a main document.
    lonely = AgreementCoverage(
        "6765/06", "6765/06", SOFTWARE, "IBM Svenska AB", CoverageStatus.NOT_COVERED, (), (), ()
    )

    found = groups([*coverages, lonely])

    assert [(g.status, g.files, [a.agreement_number for a in g.agreements]) for g in found] == [
        (
            CoverageStatus.PROCUREMENT_VERSION,
            (MAIN_ITK2020,),
            ["23.3-2940-20:018", "23.3-2940-20:010"],
        ),
        (
            CoverageStatus.HELD_BACK,
            (MAIN_MICROSOFT,),
            ["23.5-3718-2024"],
        ),
        (
            CoverageStatus.HELD_BACK,
            (MAIN_ITK3,),
            ["23.3-8321-2024-001", "23.3-8321-2024-002"],
        ),
        (CoverageStatus.NOT_COVERED, (), ["6765/05"]),
        (CoverageStatus.NOT_COVERED, (), ["6765/06"]),
    ]


def test_one_finding_per_group_a_note_for_the_procurements_version() -> None:
    register = (*REGISTER, CASTRA_ITK3, CHAS_ITK3)
    files = (*PILOT_LIKE, TEMPLATE_ITK3)
    quarantine = held(CARD_018, MAIN_ITK3, MAIN_MICROSOFT)

    found = findings(run(*files, quarantine=quarantine, register=register))

    assert found == [
        Finding(
            check="coverage",
            severity=Severity.NOTE,
            subject="23.3-2940-20",
            message=(
                "2 avtal inom IT-konsulttjänster Resurskonsulter täcks bara av huvuddokument "
                "som indexeras men inte är det undertecknade avtalet: cbe12fd30683 "
                "(upphandlingens version från TendSign). Den undertecknade versionen publiceras "
                "inte på avropa.se. Avtalens egna leverantörsavtal ligger i karantän: "
                "ee6107229c37. Avtalen: 23.3-2940-20:010, 23.3-2940-20:018."
            ),
        ),
        Finding(
            check="coverage",
            severity=Severity.REPORT,
            subject="23.5-3718-2024",
            message=(
                "Avtal 23.5-3718-2024 (Microsoft AB, Programvaror och tjänster) täcks bara av "
                "huvuddokument som inte indexeras: 171a3cacf5fd (i karantän)."
            ),
            agreement_number="23.5-3718-2024",
        ),
        Finding(
            check="coverage",
            severity=Severity.REPORT,
            subject="23.3-8321-2024",
            message=(
                "2 avtal inom IT-konsulttjänster Resurskonsulter täcks bara av huvuddokument "
                "som inte indexeras: 65d611d12eab (i karantän). Avtalen: 23.3-8321-2024-001, "
                "23.3-8321-2024-002."
            ),
        ),
        Finding(
            check="coverage",
            severity=Severity.REPORT,
            subject="6765/05",
            message=(
                "Avtal 6765/05 (IBM Svenska AB, Programvaror och tjänster) har inget inläst "
                "huvuddokument: inget leverantörskort med avtalets nummer och inget "
                "huvuddokument på en sida för avtalets delområde."
            ),
            agreement_number="6765/05",
        ),
    ]
    assert [finding.key for finding in found] == [
        "coverage:note:-:23.3-2940-20",
        "coverage:report:-:23.5-3718-2024",
        "coverage:report:-:23.3-8321-2024",
        "coverage:report:-:6765/05",
    ]


def test_a_single_agreements_own_card_in_quarantine_is_named_in_the_singular() -> None:
    coverages = run(
        card(CARD_018, "23.3-2940-20:018"),
        card(CARD_010, "23.3-2940-20:010"),
        PRINTOUT_ITK2020,
        quarantine=held(CARD_018),
        register=(AFRY_018, CHAS_010),
    )

    [finding] = findings(coverages)

    # The subject is the procurement's, also for a group of one (see the next test).
    assert finding.subject == "23.3-2940-20"
    assert finding.message.endswith(
        "Den undertecknade versionen publiceras inte på avropa.se. Avtalets eget "
        "leverantörsavtal ligger i karantän: ee6107229c37."
    )


def test_a_group_keeps_its_key_when_one_agreement_is_left() -> None:
    # Both cards in quarantine, then one accepted: the acceptance of the group's note
    # must still match.
    files = (card(CARD_018, "23.3-2940-20:018"), card(CARD_010, "23.3-2940-20:010"))
    register = (AFRY_018, CHAS_010)
    both = run(*files, PRINTOUT_ITK2020, quarantine=held(CARD_018, CARD_010), register=register)
    one = run(*files, PRINTOUT_ITK2020, quarantine=held(CARD_018), register=register)

    [group_of_two], [group_of_one] = findings(both), findings(one)

    assert group_of_two.key == group_of_one.key == "coverage:note:-:23.3-2940-20"


def test_the_procurements_version_and_quarantine_have_different_keys() -> None:
    # 65d611d12eab, the IT-säkerhet template: indexed, it is the procurement's version
    # (NOTE); in quarantine, as in the pilot, its agreements are held back (REPORT). An
    # acceptance of the one must not apply to the other.
    register = (CASTRA_ITK3, CHAS_ITK3)

    [indexed] = findings(run(TEMPLATE_ITK3, register=register))
    [held_back] = findings(run(TEMPLATE_ITK3, quarantine=held(MAIN_ITK3), register=register))

    assert (indexed.subject, held_back.subject) == ("23.3-8321-2024", "23.3-8321-2024")
    assert (indexed.key, held_back.key) == (
        "coverage:note:-:23.3-8321-2024",
        "coverage:report:-:23.3-8321-2024",
    )


def test_a_group_over_two_procurements_names_both() -> None:
    # Made up: the ITK 2020 printout also linked from the IT-säkerhet page of 23.3-8321-2024.
    on_two_pages = main(MAIN_ITK2020, ITK3, "23.3-8321-2024", cover="Upphandlingsdokument")
    both = CheckedFile(
        on_two_pages.extraction,
        (*PRINTOUT_ITK2020.links, *on_two_pages.links),
        (),
        "pdf",
        1,
        (),
    )

    [finding] = findings(run(both, register=(AFRY_018, CASTRA_ITK3)))

    assert finding.subject == "23.3-2940-20, 23.3-8321-2024"
    assert finding.agreement_number is None
    assert finding.message.startswith(
        "2 avtal inom IT-konsulttjänster Resurskonsulter täcks bara av huvuddokument"
    )


def test_two_groups_of_one_procurement_are_told_apart_by_their_files() -> None:
    # Made up: printouts as the main documents of both pages of 23.3-1688-2024.
    register = (CASTRA, CGI_002, CAPGEMINI, CGI_011)
    printouts = (
        main(MAIN_ITK1, ITK1, "23.3-1688-2024", cover="Upphandlingsdokument"),
        main(MAIN_ITK5, ITK5, "23.3-1688-2024", cover="Upphandlingsdokument"),
    )

    found = findings(run(*printouts, register=register))

    assert [finding.subject for finding in found] == [
        "23.3-1688-2024 (0692da436391)",
        "23.3-1688-2024 (e31f81c753c7)",
    ]
    assert len({finding.key for finding in found}) == 2
