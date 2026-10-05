"""Tests for avtalsagent.ingestion.step3_chunk.

The documents are built from verbatim lines of avropa.se documents (2026-10-05).
"""

from avtalsagent.domain.parsed import Block, BlockKind, ParsedDocument, Section
from avtalsagent.ingestion.step3_chunk import (
    DocumentContext,
    LinkInfo,
    OutlineKind,
    chunk_document,
    chunk_sections,
    clean_blocks,
    context_header,
    document_context,
    split_sections,
)

CONTEXT = DocumentContext(agreement="IT-drift (23.3-5890-2023)", document="Allmänna villkor")
FOOTER = "23.3-5890-2023 IT-drift 2023, område Mindre"


def block(
    text: str, page: int | None = 1, kind: BlockKind = BlockKind.TEXT, level: int | None = None
) -> Block:
    return Block(kind=kind, text=text, page=page, level=level)


def heading(text: str, page: int | None = 1, level: int | None = None) -> Block:
    return block(text, page, BlockKind.HEADING, level)


def document(*blocks: Block, file_type: str = "pdf") -> ParsedDocument:
    return ParsedDocument(
        sha256="ab" * 32, file_type=file_type, parser="test", pages=(), blocks=blocks
    )


def general_terms() -> ParsedDocument:
    """Four pages of "Allmänna villkor": cover, contents, body, with a running footer."""
    pages: list[Block] = []
    for page in range(1, 5):
        pages.append(block(FOOTER, page, BlockKind.PAGE_FOOTER if page == 1 else BlockKind.TEXT))
        pages.append(block(f"Sida {page}/4", page))
    return document(
        block("Ramavtal", 1, BlockKind.TITLE),
        block("Avtalsnamn IT-drift 2023, område Mindre", 1),
        *pages[:2],
        heading("Innehåll", 2),
        block("6. Allmänna villkor 3", 2),
        block("6.1 Allmänt 3", 2),
        block("6.2 Definitioner 3", 2),
        block("6.21 Avtalsbrott och påföljder 4", 2),
        block("6.21.1 Ansvar vid Försening 4", 2),
        *pages[2:4],
        heading("6. Allmänna villkor", 3),
        heading("6.1 Allmänt", 3),
        block("Dessa Allmänna villkor utgör en del av Kammarkollegiets Ramavtal.", 3),
        heading("6.2 Definitioner", 3),
        block("Med Arbetsdag avses en i Sverige helgfri måndag till fredag 08.00-17.00.", 3),
        block("Ramavtalsleverantören ska:", 3),
        block("1. föra en förteckning över Underleverantörer,", 3, BlockKind.LIST_ITEM),
        block("2. informera Avropsberättigad om ändringar.", 3, BlockKind.LIST_ITEM),
        *pages[4:6],
        heading("6.21 Avtalsbrott och påföljder", 4),
        heading("6.21.1 Ansvar vid Försening", 4),
        block("Om Ramavtalsleverantören inte levererar i tid utgår vite.", 4),
        *pages[6:8],
    )


class TestCleanBlocks:
    def test_page_furniture_page_numbers_and_contents_are_removed(self) -> None:
        texts = [b.text for b in clean_blocks(general_terms())]
        assert FOOTER not in texts  # labelled on page 1, repeated as text on the others
        assert not any(text.startswith("Sida ") for text in texts)
        assert "6.1 Allmänt 3" not in texts  # contents entry
        assert "6.1 Allmänt" in texts  # the heading itself stays

    def test_page_labels_of_forms_and_printouts_are_removed(self) -> None:
        labels = [
            "Utskrivet: 2021-02-09 12:21 Sida 5 av 111",
            "Datum Sid 2 (27)",
            "MBSA20201Agr(WW)(SWE)(Oct2019) Page 1 of 8",
            "Z126-6304-SE-8 03-2018 Sidan 1 av 5",
        ]
        kept = "Leveransen ska vara klar inom 30 dagar, se sida 4."
        texts = [b.text for b in clean_blocks(document(*map(block, [*labels, kept])))]
        assert texts == [kept]

    def test_a_line_on_few_pages_is_kept(self) -> None:
        blocks = [block("Ramavtalsleverantören ska ha en försäkring.", page) for page in (1, 2)]
        blocks += [block(f"Text på sidan {page}", page) for page in range(3, 11)]
        assert len(clean_blocks(document(*blocks))) == 10


def questions_log() -> ParsedDocument:
    """A TendSign "Frågor och svar" printout whose questions quote the tender."""
    return document(
        block("Frågor och svar - Upphandlingsdokument", 1, BlockKind.TITLE),
        block("1 Publik fråga", 1),
        block("5.6.3.1 Kvalitetsledningssystem", 1),
        block("Avses ett certifierat ledningssystem?", 1),
        block("Publikt svar 2024-09-30 14:32", 1),
        block("Nej, se punkt 5.6.3.1.", 1),
        block("2 Publik fråga", 2),
        block("5.6.3.2 Miljöledningssystem", 2),
        block("Gäller samma sak för miljöledningssystemet?", 2),
        block("Privat fråga", 2),
        block("Publikt informationsmeddelande", 2),
        block("Sista dag för frågor är passerad.", 2),
    )


class TestSplitSections:
    def test_a_questions_log_is_split_per_question_not_by_quoted_numbers(self) -> None:
        outline, sections = split_sections(questions_log())
        assert outline is OutlineKind.QUESTIONS
        assert [(s.number, s.title, s.level) for s in sections] == [
            (None, "Frågor och svar - Upphandlingsdokument", 0),
            ("1", "Publik fråga", 1),
            ("2", "Publik fråga", 1),
            (None, "Privat fråga", 1),
            (None, "Publikt informationsmeddelande", 1),
        ]
        assert "5.6.3.1 Kvalitetsledningssystem\n\nAvses" in sections[1].text

    def test_numbered_sections_with_levels_parents_and_paths(self) -> None:
        outline, sections = split_sections(general_terms())
        assert outline is OutlineKind.NUMBERED
        assert [(s.number, s.title, s.level) for s in sections] == [
            (None, "Ramavtal", 0),
            ("6", "Allmänna villkor", 1),
            ("6.1", "Allmänt", 2),
            ("6.2", "Definitioner", 2),
            ("6.21", "Avtalsbrott och påföljder", 2),
            ("6.21.1", "Ansvar vid Försening", 3),
        ]
        by_number = {s.number: s for s in sections}
        assert by_number["6.21.1"].parent == by_number["6.21"].position
        assert by_number["6"].parent is None
        assert by_number["6.21.1"].path == (
            "6 Allmänna villkor",
            "6.21 Avtalsbrott och påföljder",
            "6.21.1 Ansvar vid Försening",
        )

    def test_a_section_runs_to_the_next_heading_and_keeps_its_list(self) -> None:
        _, sections = split_sections(general_terms())
        definitions = next(s for s in sections if s.number == "6.2")
        assert definitions.text.startswith("6.2 Definitioner\n\nMed Arbetsdag avses")
        assert "2. informera Avropsberättigad om ändringar." in definitions.text
        assert (definitions.page_start, definitions.page_end) == (3, 3)

    def test_the_numbered_list_does_not_become_sections(self) -> None:
        _, sections = split_sections(general_terms())
        assert [s.number for s in sections if s.number] == ["6", "6.1", "6.2", "6.21", "6.21.1"]

    def test_a_number_alone_on_its_line_is_joined_with_its_title(self) -> None:
        _, sections = split_sections(
            document(
                block("6.21.9"),
                block("Ramavtalsleverantörens uppsägningsrätt"),
                block("Ramavtalsleverantören får säga upp Kontraktet."),
                heading("6.21.10 Kontraktets förtida upphörande av annan anledning"),
            )
        )
        assert sections[0].number == "6.21.9"
        assert sections[0].text.startswith("6.21.9 Ramavtalsleverantörens uppsägningsrätt\n\n")

    def test_a_long_heading_is_a_clause_and_its_title_is_cut(self) -> None:
        clause = (
            "3.1 Personuppgiftsbiträdesavtalet utgör en bilaga till avropet från ramavtalet "
            "Programvaror och tjänster – Programvarulösningar och gäller under hela avtalstiden."
        )
        _, sections = split_sections(
            document(heading("3 Allmänt"), heading(clause), heading("3.2 Begrepp"))
        )
        title = sections[1].title
        assert title.endswith(" …") and len(title) <= 122
        assert sections[1].text == clause  # the whole clause stays in the text

    def test_parser_headings_are_used_when_there_are_no_numbers(self) -> None:
        outline, sections = split_sections(
            document(
                heading("Universella licensvillkor"),
                block("Dessa villkor gäller för alla produkter."),
                heading("Onlinetjänster"),
                block("Kunden får använda onlinetjänsterna."),
            )
        )
        assert outline is OutlineKind.HEADINGS
        assert [(s.number, s.title, s.level) for s in sections] == [
            (None, "Universella licensvillkor", 1),
            (None, "Onlinetjänster", 1),
        ]

    def test_numbers_that_start_late_are_not_the_outline(self) -> None:
        body = [block(f"Kunden får använda produkten enligt villkor {n}.") for n in range(20)]
        outline, sections = split_sections(
            document(
                *body,
                block("1 Använda Azure-förskottsbetalning"),
                block("2 Fakturera Azure-förskottsbetalning"),
            )
        )
        assert outline is OutlineKind.NONE
        assert len(sections) == 1

    def test_a_document_without_headings_is_one_section(self) -> None:
        outline, sections = split_sections(
            document(block("Timpriser"), block("Konsult 1 | 950 kr"))
        )
        assert outline is OutlineKind.NONE
        assert [(s.number, s.title, s.level, s.text) for s in sections] == [
            (None, "Text före första rubriken", 0, "Timpriser\n\nKonsult 1 | 950 kr")
        ]

    def test_an_unnumbered_word_part_after_the_numbered_sections(self) -> None:
        _, sections = split_sections(
            document(
                heading("Innehåll", None, 1),
                heading("15 Ansvar för skada i samband med behandling", None, 1),
                block("Part ansvarar för skada.", None),
                heading("16 Tvistelösning", None, 1),
                heading("16.1 Bestämmelser om tvist regleras i ramavtalet.", None, 2),
                heading("Instruktion till Personuppgiftsbiträdesavtalet", None, 1),
                heading(
                    "Avsnitt 1 Behandling som omfattas av Personuppgiftsbiträdesavtalet", None, 2
                ),
                block("Denna del utgör den personuppgiftsansvariges instruktioner.", None),
                file_type="docx",
            )
        )
        assert [(s.number, s.title, s.level) for s in sections] == [
            (None, "Text före första rubriken", 0),
            ("15", "Ansvar för skada i samband med behandling", 1),
            ("16", "Tvistelösning", 1),
            ("16.1", "Bestämmelser om tvist regleras i ramavtalet.", 2),
            (None, "Instruktion till Personuppgiftsbiträdesavtalet", 1),
        ]
        assert sections[-1].text.endswith("den personuppgiftsansvariges instruktioner.")
        assert sections[-1].page_start is None


class TestChunks:
    def section(self, text: str, number: str | None = "6.2", position: int = 1) -> Section:
        return Section(
            position=position,
            number=number,
            title="Definitioner",
            level=2,
            parent=0,
            path=("6 Allmänna villkor", "6.2 Definitioner"),
            page_start=3,
            page_end=3,
            text=text,
        )

    def test_a_short_section_is_one_chunk_with_a_context_header(self) -> None:
        [chunk] = chunk_sections(
            [self.section("6.2 Definitioner\n\nMed Arbetsdag avses ...")], CONTEXT
        )
        assert chunk.context_header == (
            "IT-drift (23.3-5890-2023) › Allmänna villkor › 6 Allmänna villkor › 6.2 Definitioner"
        )
        assert (chunk.section, chunk.position) == (1, 0)

    def test_a_long_section_is_cut_between_paragraphs(self) -> None:
        paragraphs = [
            f"Stycke {n}. " + "Ramavtalsleverantören ska leverera. " * 10 for n in range(10)
        ]
        text = "6.2 Definitioner\n\n" + "\n\n".join(paragraphs)
        chunks = chunk_sections([self.section(text)], CONTEXT, max_chars=1000)
        assert len(chunks) > 1
        assert all(len(c.text) <= 1000 for c in chunks)
        assert [c.position for c in chunks] == list(range(len(chunks)))
        assert "\n\n".join(c.text for c in chunks) == text  # nothing lost or reordered

    def test_a_very_long_paragraph_is_cut_between_sentences(self) -> None:
        text = " ".join(f"Mening nummer {n} om vite vid försening." for n in range(100))
        chunks = chunk_sections([self.section(text)], CONTEXT, max_chars=300)
        assert all(len(c.text) <= 300 for c in chunks)
        assert all(c.text.endswith("försening.") for c in chunks)
        assert " ".join(c.text for c in chunks) == text

    def test_a_heading_without_body_gets_no_chunk(self) -> None:
        assert chunk_sections([self.section("6 Allmänna villkor", number="6")], CONTEXT) == []

    def test_a_clause_heading_without_body_gets_a_chunk(self) -> None:
        clause = "16.1 Bestämmelser om tvist regleras i ramavtalet."
        assert [c.text for c in chunk_sections([self.section(clause, number="16.1")], CONTEXT)] == [
            clause
        ]


def test_chunk_document_returns_sections_and_chunks() -> None:
    result = chunk_document(general_terms(), CONTEXT)
    assert result.outline is OutlineKind.NUMBERED
    assert len(result.sections) == 6
    # "6 Allmänna villkor" and "6.21" are only headings; the other four have text.
    assert sorted({c.section for c in result.chunks}) == [0, 2, 3, 5]


def test_context_header_of_the_text_before_the_first_heading() -> None:
    section = split_sections(general_terms())[1][0]
    assert context_header(CONTEXT, section) == "IT-drift (23.3-5890-2023) › Allmänna villkor"


class TestDocumentContext:
    def link(self, **changes: object) -> LinkInfo:
        values: dict[str, object] = {
            "title": "Allmänna villkor",
            "agreement_number": None,
            "supplier_name": None,
            "page_title": "IT-drift Mindre, upp till 200 anställda",
            "procurement_numbers": ("23.3-5890-2023",),
            "framework_areas": ("IT-drift",),
        }
        return LinkInfo(**(values | changes))  # type: ignore[arg-type]

    def test_one_area_and_procurement(self) -> None:
        assert document_context([self.link()]) == DocumentContext(
            "IT-drift (23.3-5890-2023)", "Allmänna villkor"
        )

    def test_a_template_linked_from_several_areas(self) -> None:
        links = [
            self.link(title="Utkast till Säkerhetsskyddsavtal (Nivå 1)"),
            self.link(
                title="Utkast till Säkerhetssyddsavtal (Nivå 1)",  # a typo on one page
                procurement_numbers=("23.3-14537-2023",),
                framework_areas=("Bemanningstjänster",),
            ),
            self.link(
                title="Utkast till Säkerhetsskyddsavtal (Nivå 1)",
                procurement_numbers=("23.3-10639-2023",),
            ),
        ]
        assert document_context(links) == DocumentContext(
            "Bemanningstjänster, IT-drift", "Utkast till Säkerhetsskyddsavtal (Nivå 1)"
        )

    def test_a_suppliers_own_agreement(self) -> None:
        link = self.link(
            title="Ramavtal",
            agreement_number="23.3-2940-20:010",
            supplier_name="Konsultbolaget AB",
            framework_areas=("IT-konsulttjänster Resurskonsulter",),
            procurement_numbers=("23.3-2940-20",),
        )
        assert document_context([link]) == DocumentContext(
            "IT-konsulttjänster Resurskonsulter (23.3-2940-20)",
            "Ramavtal 23.3-2940-20:010 (Konsultbolaget AB)",
        )

    def test_a_procurement_missing_from_the_register_uses_the_page_title(self) -> None:
        link = self.link(
            framework_areas=(), page_title="Volymavtal för IBM", procurement_numbers=("6765/05",)
        )
        assert document_context([link]).agreement == "Volymavtal för IBM (6765/05)"
