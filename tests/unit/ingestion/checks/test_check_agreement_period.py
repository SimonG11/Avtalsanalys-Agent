"""Tests for avtalsagent.ingestion.checks.agreement_period.

The document lines are real, from the pilot files of the M4 survey (dates.md),
cited as sha[:12] §section or block, and go through `find_dates` as in step 4.
The pages, their periods and their sub-areas are the real ones of avropa.se
(2026-10-05), and so are the register dates unless a test says it changes them
to make a deviation. Supplier names and organisation numbers are made up, and
so are the few settings a test marks as made up (a real line in a file type it
is not from, a near-miss page title).
"""

from dataclasses import dataclass
from datetime import date

from avtalsagent.domain.documents import CatalogLink
from avtalsagent.domain.extracted import (
    DocumentExtraction,
    DocumentMetadata,
    DocumentType,
    Finding,
    Severity,
)
from avtalsagent.domain.parsed import Block, BlockKind
from avtalsagent.domain.register import RegisterEntry
from avtalsagent.ingestion.checks.agreement_period import CHECK, run
from avtalsagent.ingestion.checks.context import CheckContext, CheckedFile
from avtalsagent.ingestion.extract.dates import find_dates

AVROPA = "https://www.avropa.se/ramavtal/ramavtalsomraden/"
TABLE = BlockKind.TABLE

# 185872a6bb90 §1.8 (b69, b70): a signed card of Informationsförsörjning.
IN_FORCE = (
    "Ramavtalet blir bindande från och med det datumet som Parterna signerar det men träder "
    "i kraft 2023-02-27. Om Parterna signerar Ramavtalet vid ett senare datum träder "
    "Ramavtalet i kraft från och med det datumet. Att Ramavtalet träder i kraft innebär att "
    "Avrop kan göras."
)
LENGTH_AND_END = (
    "Ramavtalet löper under en period av 48 månader, dock längst till och med den "
    "2027-02-26. Ramavtalet upphör därefter att gälla utan uppsägning. Tilldelning av "
    "Kontrakt ska dock ske under Ramavtalets löptid."
)
# 14aa1cc8ee3d §9.6.2 (b120) and §9.6.3 (b122): a signed IT-konsulttjänster 2020 card.
EARLIEST = (
    "Ramavtalet träder i kraft, vilket innebär att Avrop kan göras under Ramavtalet, den dag "
    "det signerats av båda Parter samt tidigast från och med den 2022-12-01. Från 2022-12-01 "
    "löper ramavtalet därefter under en period av 24 månader. Ramavtalet upphör därefter att "
    "gälla utan uppsägning."
)
EXTENSION = (
    "Eventuell förlängning av Ramavtal sker på initiativ av Kammarkollegiet. "
    "Ramavtalsleverantören har inte rätt att motsätta sig förlängning. Oaktat vad som står i "
    "Ramavtalets sidhuvud, kan en eller flera förlängningar av Ramavtalets giltighetstid uppgå "
    "till maximalt 24 månader. Förlängning regleras skriftligen."
)
# f479352f0a55 b1: the TendSign cover of the signed Systemutveckling main document.
SYSTEMUTVECKLING_COVER = (
    "Avtalsnamn | Programvaror och tjänster - | Startdatum | 2023-11-01\n"
    " | Systemutveckling | Slutdatum | 2027-10-31\n"
    "Ref. nr. | 23.3-2651-2022 | Förlängning | Ingen förlängning"
)
# 76dfb5d1ae1f b2: the cover of the signed IT-drift Större main document.
STORRE_COVER = (
    "Avtalsnamn IT-drift 2023, område Större | Startdatum 2024-11-14\n"
    "Ref. nr. 23.3-10639-2023 | Slutdatum 2028-11-13\n"
    " | Förlängning Ingen förlängning"
)
# f479352f0a55 b65: a length alone.
LENGTH_ONLY = (
    "Ramavtalet löper under en period av 48 månader. Ramavtalet upphör därefter att gälla "
    "utan uppsägning. Tilldelning av Kontrakt ska dock ske under Ramavtalets löptid."
)
# 4f886a784c5d b98 and b102: the guide of both IT-drift pages.
GUIDE_IN_FORCE = (
    "Ramavtalet träder i kraft 2024-11-14. Att Ramavtalet träder i kraft innebär att Avrop "
    "kan göras."
)
GUIDE_END = (
    "Ramavtalet löper i 48 månader och som längst till och med 2028-11-13. Ramavtalet upphör "
    "därefter att gälla utan uppsägning."
)
# 4b6c2a533fae b39 and b41: the IT-konsulttjänster guide, one item per sub-area.
AREA_1 = (
    "- Ramavtalet för område 1 - Verksamhetens IT-behov är giltigt från och med 2025-08-19 "
    "till och med 2029-08-18."
)
AREA_3 = (
    "- Ramavtalet för område 3 - IT-säkerhet är giltigt från och med 2026-03-10 och till och "
    "med 2030-03-09."
)
# 4b6c2a533fae b16, its version table (the last row shortened after the period).
VERSIONS = (
    "| Publicerat datum | Uppdaterat avsnitt\n"
    "1.00 | 2025-08-19 | Första slutversion\n"
    "1.01 | 2025-08-23 | Uppdaterad version när nya AO5 träder i kraft per 2025-08-23.\n"
    "1.02 | 2026-03-10 | Uppdaterad version för nya ramavtalet IT-säkerhet, AO3 träder i kraft "
    "per 2026-03-10. Nytt för IT-säkerhet (AO3): - avtalstiden (2026-03-10- 2030-03-09)"
)
# 49f36699a469 b83: the Programvaror och tjänster guide, one row per sub-area.
AREA_TABLE = (
    "Ramavtalområde | Avtalsperiod\n"
    "Programvaror och Tjänster Licenser och licenstjänster | 2023-02-20 - 2027-02-19\n"
    "Programvaror och Tjänster Informationsförsörjning | 2023-02-27 - 2027-02-26\n"
    "Programvaror och Tjänster Systemutveckling | 2023-11-01 - 2027-10-31\n"
    "Programvaror och Tjänster Programvarulösningar | 2023-02-18 - 2027-02-17"
)
# 171a3cacf5fd b17 (page 1) and b31 (page 2): the Microsoft period, in every page footer.
MICROSOFT_FOOTER = "Volymavtalets huvuddokument 1.0 för avtalsperiod 2024-05-01 - 2027-04-30"
# c59dbfeeb576 §1.8 (b183): a planned start in the Bemanningstjänster procurement document.
PLANNED = (
    "Ramavtalet beräknas träda i kraft 2025-04-03, om upphandlingen inte blir föremål för "
    "överprövning, och löper därefter under en period av högst 48 månader."
)


@dataclass(frozen=True)
class Page:
    title: str
    url: str
    procurement: str
    period: str | None
    area: str  # the framework area, the first level of the sub-area path


INFORMATION = Page(
    "Informationsförsörjning",
    AVROPA + "programvaror-och-tjanster/Programvaror-och-tjanster/informationsforsorjning/",
    "23.3-2649-2022",
    "2023-02-27 - 2027-02-26",
    "Programvaror och tjänster",
)
LICENCES = Page(
    "Licenser och licenstjänster",
    AVROPA + "programvaror-och-tjanster/Programvaror-och-tjanster/licenser-och-licenstjanster/",
    "23.3-2650-2022",
    "2023-02-20 - 2027-02-19",
    "Programvaror och tjänster",
)
SYSTEM = Page(
    "Systemutveckling",
    AVROPA + "programvaror-och-tjanster/Programvaror-och-tjanster/systemutveckling/",
    "23.3-2651-2022",
    "2023-11-01 - 2027-10-31",
    "Programvaror och tjänster",
)
MICROSOFT = Page(
    "Volymavtal för Microsoft",
    AVROPA + "programvaror-och-tjanster/Programvaror-och-tjanster/volymavtal-for-microsoft/",
    "23.5-3718-2024",
    "2024-05-01 - 2027-04-30",
    "Programvaror och tjänster",
)
MINDRE = Page(
    "IT-drift Mindre, upp till 200 anställda",
    AVROPA + "datacenter-och-it-driftstjanster/it-drift/it-drift-mindre-upp-till-200-anstallda/",
    "23.3-5890-2023",
    "2024-11-14 - 2028-11-13",
    "IT-drift",
)
STORRE = Page(
    "IT-drift Större, fler än 200 anställda",
    AVROPA + "datacenter-och-it-driftstjanster/it-drift/it-drift-storre-fler-an-200-anstallda/",
    "23.3-10639-2023",
    "2024-11-14 - 2028-11-13",
    "IT-drift",
)
_ITK = AVROPA + "konsulttjanster---it-och-management/it-konsulttjanster-resurskonsulter/"
ITK_1 = Page(
    "IT-konsulttjänster 1. Verksamhetens IT-behov",
    _ITK + "it-konsulttjanster-1.-verksamhetens-it-behov",
    "23.3-1688-2024",
    "2025-08-19 - 2029-08-18",
    "IT-konsulttjänster Resurskonsulter",
)
ITK_2 = Page(
    "IT-konsulttjänster 2. Ledning av IT-projekt",
    AVROPA + "konsulttjanster---it-och-management/it-konsulttjanster/"
    "it-konsulttjanster-2.-ledning-av-it-projekt",
    "23.3-2940-20",
    "2022-12-01 - 2026-11-30",
    "IT-konsulttjänster Resurskonsulter",
)
ITK_3 = Page(
    "IT-konsulttjänster 3. IT-säkerhet",
    _ITK + "it-konsulttjanster-3.-it-sakerhet",
    "23.3-8321-2024",
    "2026-03-10 - 2030-03-09",
    "IT-konsulttjänster Resurskonsulter",
)
ITK_5 = Page(
    "IT-konsulttjänster 5. IT-konsultlösningar",
    _ITK + "it-konsulttjanster-5.-it-konsultlosningar",
    "23.3-1688-2024",
    "2025-08-23 - 2029-08-22",
    "IT-konsulttjänster Resurskonsulter",
)
_BEMANNING = AVROPA + "konsulttjanster---bemanning-och-rekrytering/bemanningstjanster/"
BEMANNING_IT = Page(
    "Bemanningstjänster - IT-tjänster upp till 1000 timmar",
    _BEMANNING + "bemanningstjanster---it-tjanster-upp-till-1000-timmar/",
    "23.3-14537-2023",
    "2025-04-03 - 2029-04-02",
    "Bemanningstjänster",
)
BEMANNING_OFFICE = Page(
    "Bemanningstjänster - Kontorstjänster upp till 1000 timmar",
    _BEMANNING + "bemanningstjanster---kontorstjanster-upp-till-1000-timmar/",
    "23.3-14537-2023",
    "2025-04-22 - 2029-04-21",
    "Bemanningstjänster",
)
BEMANNING_OFFICE_LARGE = Page(
    "Bemanningstjänster - Kontorstjänster överstigande 1000 timmar",
    _BEMANNING + "bemanningstjanster---kontorstjanster-overstigande-1000-timmar2/",
    "23.3-14537-2023",
    "2025-04-22 - 2029-04-21",
    "Bemanningstjänster",
)


def block(text: str, kind: BlockKind = BlockKind.TEXT, page: int | None = 1) -> Block:
    return Block(kind=kind, text=text, page=page)


def link(
    page: Page, sha256: str, title: str = "Ramavtalets huvuddokument", card: str | None = None
) -> CatalogLink:
    return CatalogLink(
        sha256=sha256,
        url=f"https://www.avropa.se/globalassets/{sha256[:8]}.pdf",
        title=title,
        category=None if card else "Avtal",
        agreement_number=card,
        site_updated=None,
        page_url=page.url,
        page_title=page.title,
        page_procurement_numbers=(page.procurement,),
        page_period=page.period,
    )


def entry(
    page: Page,
    sequence: str,
    valid_from: str,
    valid_to: str,
    max_extension_to: str | None = None,
    agreement: str | None = None,
) -> RegisterEntry:
    """A register row of the page's sub-area; `agreement` overrides the number's spelling."""
    return RegisterEntry(
        agreement_number=agreement or f"{page.procurement}-{sequence}",
        procurement_number=page.procurement,
        org_number="556677-8899",
        supplier_name="Exempelleverantören AB",
        former_supplier_name=None,
        framework_area=page.area,
        sub_area_path=(page.area, page.title),
        valid_from=date.fromisoformat(valid_from),
        valid_to=date.fromisoformat(valid_to),
        max_extension_to=date.fromisoformat(max_extension_to) if max_extension_to else None,
    )


def page_entry(page: Page, sequence: str = "001") -> RegisterEntry:
    """A register row with the page's own period."""
    start, end = str(page.period).split(" - ")
    return entry(page, sequence, start, end)


def document(
    blocks: list[Block],
    links: list[CatalogLink],
    document_type: DocumentType = DocumentType.MAIN_DOCUMENT,
    is_template: bool = False,
    cover: str | None = None,
) -> CheckedFile:
    metadata = DocumentMetadata(
        sha256=links[0].sha256,
        title=links[0].title,
        document_type=document_type,
        type_rule="R01",
        agreement_number=links[0].agreement_number,
        annex_number=None,
        first_chapter=None,
        tendsign_cover=cover,
        is_template=is_template,
        version_date=None,
        version_rule=None,
        published_on=None,
        site_updated=None,
    )
    extraction = DocumentExtraction(metadata=metadata, facts=tuple(find_dates(blocks)), mentions=())
    return CheckedFile(
        extraction=extraction,
        links=tuple(links),
        sections=(),
        file_type="pdf",
        page_count=1,
        ocr_pages=(),
    )


def check(files: list[CheckedFile], register: list[RegisterEntry]) -> list[Finding]:
    """The findings about the files only (their pages' periods are the register's here)."""
    context = CheckContext(
        files=tuple(files),
        links=tuple(link for file in files for link in file.links),
        register=tuple(register),
        areas=(),
    )
    return [finding for finding in run(context) if finding.sha256 is not None]


SHA = "185872a6bb90" + "0" * 52


class TestSupplierCards:
    def test_a_card_that_states_its_agreements_period_gives_nothing(self) -> None:
        card = link(INFORMATION, SHA, "Ramavtal", card="23.3-2649-2022-003")
        file = document([block(IN_FORCE), block(LENGTH_AND_END)], [card])

        assert check([file], [entry(INFORMATION, "003", "2023-02-27", "2027-02-26")]) == []

    def test_an_extension_stated_in_the_next_clause_explains_a_later_end(self) -> None:
        # 2022-12-01 + 24 months is 2024-11-30; the register has the extended end.
        card = link(ITK_2, SHA, "Ramavtal", card="23.3-2940-20:026")
        file = document([block(EARLIEST), block(EXTENSION)], [card])
        register = [entry(ITK_2, "", "2022-12-01", "2026-11-30", agreement="23.3-2940-20:026")]

        assert check([file], register) == []

    def test_an_end_beyond_the_extension_quarantines_the_card(self) -> None:
        # Register end changed: 2027-11-30 is 36 months after the first period.
        card = link(ITK_2, SHA, "Ramavtal", card="23.3-2940-20:026")
        file = document([block(EARLIEST), block(EXTENSION)], [card])
        register = [entry(ITK_2, "", "2022-12-01", "2027-11-30", agreement="23.3-2940-20:026")]

        [finding] = check([file], register)

        assert finding.check == CHECK
        assert finding.severity is Severity.QUARANTINE
        assert finding.subject == "2022-12-01 - 2024-11-30"
        assert finding.message == (
            "Dokumentet anger avtalsperioden 2022-12-01 - 2024-11-30 med förlängning högst 24 "
            "månader, men registret har 2022-12-01 - 2027-11-30 för avtal 23.3-2940-20:026."
        )
        assert finding.sha256 == SHA
        assert finding.agreement_number == "23.3-2940-20:026"
        assert finding.evidence == (
            "tidigast från och med den 2022-12-01 … "
            "löper ramavtalet därefter under en period av 24 månader"
        )

    def test_without_an_extension_the_later_end_deviates(self) -> None:
        card = link(ITK_2, SHA, "Ramavtal", card="23.3-2940-20:026")
        file = document([block(EARLIEST)], [card])
        register = [entry(ITK_2, "", "2022-12-01", "2026-11-30", agreement="23.3-2940-20:026")]

        assert [f.severity for f in check([file], register)] == [Severity.QUARANTINE]

    def test_the_registers_extension_limit_explains_it_when_the_file_states_none(self) -> None:
        # "Max förl. till" is empty on every pilot row; 23.3-2965-20 has it equal to the end.
        card = link(ITK_2, SHA, "Ramavtal", card="23.3-2940-20:026")
        file = document([block(EARLIEST)], [card])
        register = [
            entry(ITK_2, "", "2022-12-01", "2026-11-30", "2026-11-30", agreement="23.3-2940-20:026")
        ]

        assert check([file], register) == []

    def test_a_cover_without_extension_is_not_saved_by_another_clause(self) -> None:
        # Made up from two real lines: the Systemutveckling cover ("Ingen förlängning") and
        # the extension clause of 14aa1cc8ee3d; the register end changed to 2028-10-31.
        file = document(
            [block(SYSTEMUTVECKLING_COVER, TABLE), block(EXTENSION)], [link(SYSTEM, SHA)]
        )
        register = [entry(SYSTEM, "001", "2023-11-01", "2028-10-31")]

        [finding] = check([file], register)

        assert finding.severity is Severity.QUARANTINE
        assert "2023-11-01 - 2027-10-31 utan förlängning" in finding.message

    def test_an_agreement_that_starts_later_with_the_same_end_is_a_note(self) -> None:
        # Register start changed to 2023-03-15; the clause allows a later signature.
        card = link(INFORMATION, SHA, "Ramavtal", card="23.3-2649-2022-003")
        file = document([block(IN_FORCE), block(LENGTH_AND_END)], [card])

        [finding] = check([file], [entry(INFORMATION, "003", "2023-03-15", "2027-02-26")])

        assert finding.severity is Severity.NOTE
        assert finding.subject == "från 2023-02-27"
        assert finding.message == (
            "Dokumentet anger avtalsperioden från 2023-02-27, och registret har 2023-03-15 - "
            "2027-02-26 för avtal 23.3-2649-2022-003: avtalet började senare än dokumentet anger."
        )

    def test_an_agreement_that_starts_earlier_deviates(self) -> None:
        card = link(INFORMATION, SHA, "Ramavtal", card="23.3-2649-2022-003")
        file = document([block(IN_FORCE), block(LENGTH_AND_END)], [card])

        [finding] = check([file], [entry(INFORMATION, "003", "2023-02-01", "2027-02-26")])

        assert finding.severity is Severity.QUARANTINE

    def test_a_card_is_compared_with_its_agreement_not_with_its_page(self) -> None:
        # Register start of another agreement of the page changed to 2023-02-01: the page's
        # sub-area starts then, the card's own agreement on the stated day.
        card = link(INFORMATION, SHA, "Ramavtal", card="23.3-2649-2022-003")
        file = document([block(IN_FORCE), block(LENGTH_AND_END)], [card])
        register = [
            entry(INFORMATION, "003", "2023-02-27", "2027-02-26"),
            entry(INFORMATION, "004", "2023-02-01", "2027-02-26"),
        ]

        assert check([file], register) == []

    def test_of_two_findings_with_one_key_the_more_severe_is_kept(self) -> None:
        # Made up: one file in two cards; one agreement starts later (NOTE), the other
        # before the stated start (QUARANTINE), so both are about "från 2023-02-27".
        cards = [
            link(INFORMATION, SHA, "Ramavtal", card="23.3-2649-2022-003"),
            link(INFORMATION, SHA, "Ramavtal", card="23.3-2649-2022-004"),
        ]
        file = document([block(IN_FORCE)], cards)
        register = [
            entry(INFORMATION, "003", "2023-03-15", "2027-02-26"),
            entry(INFORMATION, "004", "2023-02-01", "2027-02-26"),
        ]

        assert [f.severity for f in check([file], register)] == [Severity.QUARANTINE]


class TestOtherFiles:
    def test_a_main_document_is_compared_with_its_pages_sub_area(self) -> None:
        # 23.3-2651-2022-003 starts 2023-11-09, the others 2023-11-01: the sub-area starts
        # 2023-11-01, as the cover says.
        file = document([block(SYSTEMUTVECKLING_COVER, TABLE)], [link(SYSTEM, SHA)])
        register = [
            entry(SYSTEM, "001", "2023-11-01", "2027-10-31"),
            entry(SYSTEM, "003", "2023-11-09", "2027-10-31"),
        ]

        assert check([file], register) == []

    def test_a_deviating_cover_quarantines_and_names_the_page(self) -> None:
        # Register end changed to 2028-11-30.
        file = document([block(STORRE_COVER, TABLE)], [link(STORRE, SHA)])

        [finding] = check([file], [entry(STORRE, "001", "2024-11-14", "2028-11-30")])

        assert finding.severity is Severity.QUARANTINE
        assert finding.subject == "2024-11-14 - 2028-11-13"
        assert finding.message == (
            "Dokumentet anger avtalsperioden 2024-11-14 - 2028-11-13 utan förlängning, men "
            "registret har 2024-11-14 - 2028-11-30 för delområdet IT-drift Större, fler än 200 "
            "anställda."
        )
        assert finding.page_url == STORRE.url
        assert finding.agreement_number is None

    def test_a_sub_area_that_started_later_than_the_cover_is_a_note(self) -> None:
        # Register start changed: every agreement of the sub-area starts 2023-11-09.
        file = document([block(SYSTEMUTVECKLING_COVER, TABLE)], [link(SYSTEM, SHA)])

        [finding] = check([file], [entry(SYSTEM, "003", "2023-11-09", "2027-10-31")])

        assert finding.severity is Severity.NOTE

    def test_an_unscoped_statement_goes_with_all_pages_of_the_same_period(self) -> None:
        file = document(
            [block(GUIDE_IN_FORCE), block(GUIDE_END)], [link(MINDRE, SHA), link(STORRE, SHA)]
        )

        assert check([file], [page_entry(MINDRE), page_entry(STORRE)]) == []

    def test_an_unscoped_statement_is_skipped_when_the_pages_have_different_periods(self) -> None:
        # Register end of Större changed to 2028-11-30: the guide cannot be right for both.
        file = document(
            [block(GUIDE_IN_FORCE), block(GUIDE_END)], [link(MINDRE, SHA), link(STORRE, SHA)]
        )
        register = [page_entry(MINDRE), entry(STORRE, "001", "2024-11-14", "2028-11-30")]

        assert check([file], register) == []

    def test_on_one_page_of_a_different_period_the_same_statement_deviates(self) -> None:
        file = document([block(GUIDE_IN_FORCE), block(GUIDE_END)], [link(STORRE, SHA)])
        register = [entry(STORRE, "001", "2024-11-14", "2028-11-30")]

        assert [f.subject for f in check([file], register)] == ["till och med 2028-11-13"]

    def test_an_area_statement_is_compared_with_the_page_of_its_number(self) -> None:
        file = document(
            [block(AREA_1, BlockKind.LIST_ITEM), block(AREA_3, BlockKind.LIST_ITEM)],
            [link(ITK_1, SHA, "Vägledning"), link(ITK_3, SHA, "Vägledning")],
        )

        assert check([file], [page_entry(ITK_1), page_entry(ITK_3)]) == []

    def test_an_area_statement_that_deviates_names_its_area_and_page(self) -> None:
        # Register end of IT-säkerhet changed to 2030-03-31.
        file = document(
            [block(AREA_1, BlockKind.LIST_ITEM), block(AREA_3, BlockKind.LIST_ITEM)],
            [link(ITK_1, SHA, "Vägledning"), link(ITK_3, SHA, "Vägledning")],
        )
        register = [page_entry(ITK_1), entry(ITK_3, "001", "2026-03-10", "2030-03-31")]

        [finding] = check([file], register)

        assert finding.subject == "område 3 - IT-säkerhet: 2026-03-10 - 2030-03-09"
        assert finding.page_url == ITK_3.url
        assert "2026-03-10 - 2030-03-09 för område 3 - IT-säkerhet" in finding.message

    def test_an_area_statement_for_no_page_of_the_file_is_skipped(self) -> None:
        # Only the page of area 1 links here; area 3's dates are not area 1's.
        file = document(
            [block(AREA_1, BlockKind.LIST_ITEM), block(AREA_3, BlockKind.LIST_ITEM)],
            [link(ITK_1, SHA, "Vägledning")],
        )

        assert check([file], [page_entry(ITK_1)]) == []

    def test_an_area_code_names_the_page_with_that_number(self) -> None:
        # "AO5 träder i kraft per 2025-08-23" and "AO3 ... (2026-03-10- 2030-03-09)".
        file = document(
            [block(VERSIONS, TABLE)],
            [link(ITK_5, SHA, "Vägledning"), link(ITK_3, SHA, "Vägledning")],
        )

        assert check([file], [page_entry(ITK_5), page_entry(ITK_3)]) == []
        # Register end of IT-säkerhet changed to 2030-03-31.
        changed = [page_entry(ITK_5), entry(ITK_3, "001", "2026-03-10", "2030-03-31")]
        assert [f.subject for f in check([file], changed)] == ["AO3: 2026-03-10 - 2030-03-09"]

    def test_a_table_row_names_the_page_whose_title_it_contains(self) -> None:
        pages = [SYSTEM, LICENCES]
        file = document(
            [block(AREA_TABLE, TABLE)], [link(page, SHA, "Vägledning") for page in pages]
        )

        assert check([file], [page_entry(page) for page in pages]) == []
        # Register start of Licenser och licenstjänster changed to 2023-01-20.
        changed = [page_entry(SYSTEM), entry(LICENCES, "001", "2023-01-20", "2027-02-19")]
        assert [f.subject for f in check([file], changed)] == [
            "Programvaror och Tjänster Licenser och licenstjänster: 2023-02-20 - 2027-02-19"
        ]

    def test_a_row_label_inside_a_page_title_names_that_page(self) -> None:
        # Made up: a one-row table labelled with the short name of the IT-säkerhet page;
        # register end changed to 2030-03-31.
        file = document(
            [block("IT-säkerhet | 2026-03-10 - 2030-03-09", TABLE)],
            [link(ITK_1, SHA, "Vägledning"), link(ITK_3, SHA, "Vägledning")],
        )
        register = [page_entry(ITK_1), entry(ITK_3, "001", "2026-03-10", "2030-03-31")]

        assert [f.page_url for f in check([file], register)] == [ITK_3.url]

    def test_an_area_number_names_no_page_with_another_number(self) -> None:
        # Made-up page titles: "11." is not area 1, and "1.5" is no area number.
        eleven = Page("IT-konsulttjänster 11. Exempel", AVROPA + "a", "23.3-1688-2024", None, "X")
        decimal = Page("Programvaror 1.5 Exempel", AVROPA + "b", "23.3-2649-2022", None, "X")
        file = document(
            [block(AREA_1, BlockKind.LIST_ITEM)],
            [link(eleven, SHA, "Vägledning"), link(decimal, SHA, "Vägledning")],
        )
        register = [
            entry(eleven, "001", "2025-08-19", "2029-08-30"),
            entry(decimal, "001", "2025-08-19", "2029-08-30"),
        ]

        assert check([file], register) == []

    def test_a_period_repeated_in_every_footer_is_one_finding(self) -> None:
        # Register end changed to 2027-05-31.
        file = document(
            [
                block(MICROSOFT_FOOTER, BlockKind.PAGE_FOOTER, page=1),
                block(MICROSOFT_FOOTER, BlockKind.PAGE_FOOTER, page=2),
            ],
            [link(MICROSOFT, SHA, "Volymavtalets huvudavtal 1.0")],
        )
        register = [entry(MICROSOFT, "", "2024-05-01", "2027-05-31", agreement="23.5-3718-2024")]

        assert [f.subject for f in check([file], register)] == ["2024-05-01 - 2027-04-30"]


class TestPlansAndTemplates:
    def test_a_length_alone_states_no_period(self) -> None:
        file = document([block(LENGTH_ONLY)], [link(SYSTEM, SHA)])

        assert check([file], [entry(SYSTEM, "001", "2023-11-09", "2027-11-08")]) == []

    def test_a_planned_start_is_never_compared(self) -> None:
        # The office pages' agreements start 2025-04-22.
        file = document(
            [block(PLANNED)],
            [link(BEMANNING_OFFICE, SHA, "Upphandlingsdokument")],
            DocumentType.PROCUREMENT_DOCUMENT,
        )

        assert check([file], [entry(BEMANNING_OFFICE, "008", "2025-04-22", "2029-04-21")]) == []

    def test_a_template_is_not_compared(self) -> None:
        # Made up: the Större cover in a file marked as a template; register end changed.
        file = document([block(STORRE_COVER, TABLE)], [link(STORRE, SHA)], is_template=True)

        assert check([file], [entry(STORRE, "001", "2024-11-14", "2028-11-30")]) == []

    def test_a_procurement_printout_that_deviates_gives_a_note(self) -> None:
        # Made up: the card's clause in a printout with the cover "Upphandlingsdokument";
        # register start changed to 2023-01-15.
        file = document([block(IN_FORCE)], [link(INFORMATION, SHA)], cover="Upphandlingsdokument")

        [finding] = check([file], [entry(INFORMATION, "001", "2023-01-15", "2027-02-26")])

        assert finding.severity is Severity.NOTE
        assert finding.message.startswith("Dokumentet är från upphandlingen")

    def test_a_procurement_document_that_deviates_gives_a_note(self) -> None:
        file = document(
            [block(IN_FORCE)],
            [link(INFORMATION, SHA, "Anbudsinbjudan")],
            DocumentType.PROCUREMENT_DOCUMENT,
        )

        [finding] = check([file], [entry(INFORMATION, "001", "2023-01-15", "2027-02-26")])

        assert finding.severity is Severity.NOTE


class TestPages:
    def report(self, pages: list[Page], register: list[RegisterEntry]) -> list[Finding]:
        file = document([], [link(page, SHA, "Allmänna villkor") for page in pages])
        context = CheckContext(files=(file,), links=file.links, register=tuple(register), areas=())
        return run(context)

    def test_a_page_whose_period_differs_from_its_sub_area_is_reported(self) -> None:
        # Bemanningstjänster: -008 has the page's dates, -028 only offices from 2025-04-03.
        register = [
            entry(BEMANNING_OFFICE, "008", "2025-04-22", "2029-04-21"),
            entry(BEMANNING_OFFICE, "028", "2025-04-03", "2029-04-02"),
        ]

        [finding] = self.report([BEMANNING_OFFICE], register)

        assert finding.severity is Severity.REPORT
        assert finding.subject == "2025-04-22 - 2029-04-21"
        assert finding.sha256 is None
        assert finding.page_url == BEMANNING_OFFICE.url
        assert finding.message == (
            "Sidans avtalsperiod 2025-04-22 - 2029-04-21 stämmer inte med registret, som har "
            "2025-04-03 - 2029-04-21 för delområdet Bemanningstjänster - Kontorstjänster upp "
            "till 1000 timmar: 1 av 2 avtal har andra datum än sidan."
        )

    def test_a_page_with_its_sub_areas_period_is_not_reported(self) -> None:
        # The IT pages of the same procurement: all agreements 2025-04-03 - 2029-04-02.
        register = [entry(BEMANNING_IT, "001", "2025-04-03", "2029-04-02")]

        assert self.report([BEMANNING_IT], register) == []

    def test_a_page_without_a_period_is_not_compared(self) -> None:
        page = Page(BEMANNING_OFFICE.title, BEMANNING_OFFICE.url, "23.3-14537-2023", None, "B")
        register = [entry(page, "028", "2025-04-03", "2029-04-02")]

        assert self.report([page], register) == []

    def test_two_pages_with_the_same_period_have_their_own_findings(self) -> None:
        pages = [BEMANNING_OFFICE, BEMANNING_OFFICE_LARGE]
        register = [entry(page, "028", "2025-04-03", "2029-04-02") for page in pages]

        findings = self.report(pages, register)

        assert [f.page_url for f in findings] == [BEMANNING_OFFICE.url, BEMANNING_OFFICE_LARGE.url]
        assert len({f.key for f in findings}) == 2
