"""Tests for avtalsagent.ingestion.step3_chunk.

The documents are built from verbatim lines of avropa.se documents (2026-10-05).
"""

import pytest

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

LIST = BlockKind.LIST_ITEM
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

    def test_a_page_header_is_removed_only_when_it_runs_over_pages(self) -> None:
        # The layout model sometimes labels the first line of a page as a header.
        sentence = (
            "Kammarkollegiet kommer att säkerställa att jäv eller intressekonflikt inte föreligger."
        )
        code = "AcademicQualEdUserDef(EMEA)(SWE)(Aug2023)"
        blocks = [
            block(code, 1, BlockKind.PAGE_FOOTER),
            block("Page 1 of 2 Document X20-11691", 1, BlockKind.PAGE_FOOTER),
            block(sentence, 2, BlockKind.PAGE_HEADER),
            block(code, 2, BlockKind.PAGE_FOOTER),
        ]
        assert [b.text for b in clean_blocks(document(*blocks))] == [sentence]

    def test_list_markers_put_last_by_the_layout_model_are_moved_first(self) -> None:
        items = [
            "Skriftliga ändringar och tillägg till säkerhetsskyddsavtal 1.",
            "Säkerhetsskyddsavtal 2.",
            "uppfyller krav ställda i Kontrakt och i Ramavtalet; a.",
            "avseende Programvara uppfyller de krav som Kund har beskrivit; b.",
            "Kontraktet med bilagor (såsom bilagorna Allmänna villkor och Servicenivåavtal) 3.",
            "sköta administrativa rutiner ·",
            "Avtalet gäller enligt punkt 6.",  # not the next number: a sentence
            "Leverans sker enligt bilaga 1.",  # a run of one: a sentence
        ]
        blocks = [block(text, 7, BlockKind.LIST_ITEM) for text in items]
        assert [b.text for b in clean_blocks(document(*blocks))] == [
            "1. Skriftliga ändringar och tillägg till säkerhetsskyddsavtal",
            "2. Säkerhetsskyddsavtal",
            "a. uppfyller krav ställda i Kontrakt och i Ramavtalet;",
            "b. avseende Programvara uppfyller de krav som Kund har beskrivit;",
            "3. Kontraktet med bilagor (såsom bilagorna Allmänna villkor och Servicenivåavtal)",
            "· sköta administrativa rutiner",
            "Avtalet gäller enligt punkt 6.",
            "Leverans sker enligt bilaga 1.",
        ]

    def test_list_markers_of_items_called_headings_are_moved_but_not_in_text(self) -> None:
        blocks = [
            heading("Miljöledningssystemets omfattning 1.", 31),
            block("Miljöledningssystemet ska innehålla en beskrivning av dess omfattning.", 31),
            heading("Miljöpolicy 2.", 31),
            block(
                "Avsnitt Upplysningar med avseende särskilt på EU-sanktioner 3.",
                32,
                BlockKind.LIST_ITEM,
            ),
            block("Referensuppdraget ska ha utförts av anbudsgivaren i steg 1.", 33),
            block("Fler referensuppdrag kan anges i detta steg 2.", 33),
        ]
        assert [b.text for b in clean_blocks(document(*blocks))] == [
            "1. Miljöledningssystemets omfattning",
            "Miljöledningssystemet ska innehålla en beskrivning av dess omfattning.",
            "2. Miljöpolicy",
            "3. Avsnitt Upplysningar med avseende särskilt på EU-sanktioner",
            "Referensuppdraget ska ha utförts av anbudsgivaren i steg 1.",
            "Fler referensuppdrag kan anges i detta steg 2.",
        ]

    def test_text_repeated_in_the_middle_of_pages_is_kept(self) -> None:
        # A running header is at the top of every page; the role description repeats
        # in the middle of every page and is body text.
        header = "23.3-14537-2023 Bemanningstjänster"
        intro = "I denna tjänst kan nedan arbetsuppgifter förekomma:"
        blocks = []
        for page in range(1, 5):
            blocks += [
                block(header, page),
                heading(f"5.1.{page} Administratör {page}", page),
                block("Arbetsuppgifter", page),
                block(intro, page),
                block(f"sköta rutiner för enhet {page}", page, BlockKind.LIST_ITEM),
                block("Kompetenskrav", page),
                block("Gymnasium eller likvärdig utbildning", page),
                block(f"Sida {page} (4)", page),
            ]
        texts = [b.text for b in clean_blocks(document(*blocks))]
        assert header not in texts
        assert texts.count(intro) == 4

    def test_the_title_of_the_removed_contents_is_removed(self) -> None:
        blocks = [heading("Innehåll", 1), heading("6.1 Innehållsförteckning", 2)]
        assert [b.text for b in clean_blocks(document(*blocks))] == ["6.1 Innehållsförteckning"]

    def test_a_line_on_few_pages_is_kept(self) -> None:
        blocks = [block("Ramavtalsleverantören ska ha en försäkring.", page) for page in (1, 2)]
        blocks += [block(f"Text på sidan {page}", page) for page in range(3, 11)]
        assert len(clean_blocks(document(*blocks))) == 10


def questions_log(first: str = "1 Publik fråga", second: str = "2 Publik fråga") -> ParsedDocument:
    """A TendSign "Frågor och svar" printout whose questions quote the tender."""
    return document(
        block("Frågor och svar - Upphandlingsdokument", 1, BlockKind.TITLE),
        block(first, 1),
        block("5.6.3.1 Kvalitetsledningssystem", 1),
        block("Avses ett certifierat ledningssystem?", 1),
        block("Publikt svar 2024-09-30 14:32", 1),
        block("Nej, se punkt 5.6.3.1.", 1),
        block(second, 2),
        block("5.6.3.2 Miljöledningssystem", 2),
        block("Gäller samma sak för miljöledningssystemet? Se svar Fråga 1.", 2),
        block("Publik fråga 502022-03-22 16:07 Enligt 4.2.2 erhåller sökanden 10 poäng", 2),
        block("Privat fråga", 2),
        block("Publikt informationsmeddelande", 2),
        block("Sista dag för frågor är passerad.", 2),
    )


class TestSplitSections:
    @pytest.mark.parametrize(
        ("first", "second"),
        [
            ("1 Publik fråga", "2 Publik fråga"),  # the text layer
            ("Publik fråga 1", "Publik fråga 2"),  # the layout model's reading order
        ],
    )
    def test_a_questions_log_is_split_per_question_not_by_quoted_numbers(
        self, first: str, second: str
    ) -> None:
        outline, sections = split_sections(questions_log(first, second))
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

    def test_headings_in_a_table_without_columns_are_found(self) -> None:
        # The layout model called this stretch a table and the table model found no
        # columns, so the parser read its lines from the text layer.
        table = block(
            "6.21 Avtalsbrott och påföljder\n"
            "Part kan göra gällande en eller flera olika\n"
            "påföljder.\n"
            "6.21.1 Ansvar vid Försening\n"
            "6.21.1.1 Vad som avses med Försening",
            65,
            BlockKind.TABLE,
        )
        _, sections = split_sections(
            document(
                heading("6.20 Uppföljning", 64),
                block("Ramavtalsleverantören ansvarar för att visa att krav uppfylls.", 64),
                table,
                block("Med Försening avses att Faktisk Startdag inträder senare.", 65),
                heading("6.21.1.2 Vite vid Försening", 66),
            )
        )
        assert [s.number for s in sections] == ["6.20", "6.21", "6.21.1", "6.21.1.1", "6.21.1.2"]
        assert sections[1].text == (
            "6.21 Avtalsbrott och påföljder\n\n"
            "Part kan göra gällande en eller flera olika påföljder."
        )

    def test_a_section_without_its_parent_number_has_no_parent(self) -> None:
        # A template whose sections 2-2.3 were deleted; its contents list 2.4.
        _, sections = split_sections(
            document(
                block("1.3 Hållbarhetsmål ........................ 3", 1),
                block("2.4 Särskilda kontraktsvillkor ............ 4", 1),
                heading("1 Inledning", 2),
                heading("1.3 Hållbarhetsmål", 2),
                heading("2.4 Särskilda kontraktsvillkor", 3),
                heading("2.4.1 Villkor för miljöhänsyn vid fullgörande av ramavtalet", 3),
                heading("3 Kravkatalog", 4),
            )
        )
        assert [s.path for s in sections] == [
            ("1 Inledning",),
            ("1 Inledning", "1.3 Hållbarhetsmål"),
            ("2.4 Särskilda kontraktsvillkor",),
            (
                "2.4 Särskilda kontraktsvillkor",
                "2.4.1 Villkor för miljöhänsyn vid fullgörande av ramavtalet",
            ),
            ("3 Kravkatalog",),
        ]

    def test_a_heading_split_over_two_blocks_is_joined(self) -> None:
        _, sections = split_sections(
            document(
                heading("5.4.1.3 Bedrägeri", 31),
                block("Har företaget dömts för bedrägeri?", 31),
                heading("5.4.1.4 Terroristbrott eller brott med anknytning till", 32),
                heading("terroristverksamhet", 32),
                block("Har företaget dömts för terroristbrott?", 32),
            )
        )
        assert sections[-1].title == (
            "Terroristbrott eller brott med anknytning till terroristverksamhet"
        )
        assert sections[-1].text.startswith(
            "5.4.1.4 Terroristbrott eller brott med anknytning till terroristverksamhet\n\nHar"
        )

    def test_numbers_printed_as_images_come_from_the_contents(self) -> None:
        # The second-level numbers are images: neither the contents nor the headings
        # have them in the text layer. The contents order gives them back.
        contents = [
            "1 Om vägledningen .............................. | 5",
            "Inledning ...................................... | 5",
            "Utveckling av vägledningen ..................... | 5",
            "2 IT-konsulttjänster ........................... | 5",
            "Avropsberättigade .............................. | 5",
            "Ramavtalets omfattning ......................... | 6",
            "2.2.1 | Delområden ............................... | 7",
        ]
        _, sections = split_sections(
            document(
                block("\n".join(contents), 2, BlockKind.TOC),
                heading("1 Om vägledningen", 5),
                heading("Inledning", 5),
                block("Vägledningen beskriver hur man avropar.", 5),
                heading("Utveckling av vägledningen", 5),
                heading("2 IT-konsulttjänster", 5),
                heading("Avropsberättigade", 5),
                heading("Ramavtalets omfattning", 6),
                heading("2.2.1 Delområden", 7),
                block("Ramavtalsområdet består av fem delområden.", 7),
            )
        )
        assert [(s.number, s.title, s.parent) for s in sections] == [
            ("1", "Om vägledningen", None),
            ("1.1", "Inledning", 0),
            ("1.2", "Utveckling av vägledningen", 0),
            ("2", "IT-konsulttjänster", None),
            ("2.1", "Avropsberättigade", 3),
            ("2.2", "Ramavtalets omfattning", 3),
            ("2.2.1", "Delområden", 5),
        ]

    def test_numbered_list_items_among_many_headings_are_not_the_outline(self) -> None:
        # Microsoft's product terms: unnumbered headings, and numbered list items.
        blocks = []
        for name in ("Azure", "Dynamics 365", "Office 365", "Windows"):
            blocks += [
                heading(f"{name}", 3),
                heading(f"Villkor för {name}", 3),
                heading(f"Licensiering av {name}", 3),
                block(f"Kunden får använda {name} enligt följande.", 3),
            ]
        blocks[2:2] = [
            block("1. Kunden har tillräckliga rättigheter.", 3, BlockKind.LIST_ITEM),
            block("2. Kunden ändrar inte programvaran.", 3, BlockKind.LIST_ITEM),
        ]
        outline, sections = split_sections(document(*blocks))
        assert outline is OutlineKind.HEADINGS
        assert [s.title for s in sections][:3] == [
            "Azure",
            "Villkor för Azure",
            "Licensiering av Azure",
        ]

    def test_headings_without_levels_take_them_from_the_contents(self) -> None:
        contents = [
            block(f"{title} ........................................ {page}", 2)
            for title, page in [("INLEDNING", 3), ("SYSTEM CENTER SERVER", 29), ("SQL SERVER", 40)]
        ]
        _, sections = split_sections(
            document(
                *contents,
                heading("Inledning", 3),
                heading("Välkommen", 3),
                heading("System Center Server", 29),
                heading("Användningsrättigheter", 29),
                block("Licensvillkor | Universella licensvillkor för all programvara", 29),
                heading("SQL Server", 40),
                heading("Användningsrättigheter", 40),
            )
        )
        assert [(s.title, s.level) for s in sections] == [
            ("Inledning", 1),
            ("Välkommen", 2),
            ("System Center Server", 1),
            ("Användningsrättigheter", 2),
            ("SQL Server", 1),
            ("Användningsrättigheter", 2),
        ]
        assert sections[3].path == ("System Center Server", "Användningsrättigheter")

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
        # "Innehåll", the title of the removed table of contents, is removed too.
        assert [(s.number, s.title, s.level) for s in sections] == [
            ("15", "Ansvar för skada i samband med behandling", 1),
            ("16", "Tvistelösning", 1),
            ("16.1", "Bestämmelser om tvist regleras i ramavtalet.", 2),
            (None, "Instruktion till Personuppgiftsbiträdesavtalet", 1),
        ]
        assert sections[-1].text.endswith("den personuppgiftsansvariges instruktioner.")
        assert sections[-1].page_start is None

    def test_word_headings_between_numbered_sections_are_their_text(self) -> None:
        # Microsoft's enrollment forms style whole sentences as Heading 1.
        _, sections = split_sections(
            document(
                heading("1 Primär kontaktperson.", None, 1),
                heading("Det Registrerade Koncernbolaget måste ange en kontaktperson.", None, 1),
                block("Namn:", None),
                heading("2 Meddelanden.", None, 1),
                heading("Välj språk för meddelanden.", None, 1),
                block("Svenska", None),
                heading("3 Registreringens löptid.", None, 1),
                block("36 månader", None),
                heading("Registreringsinformation", None, 1),
                block("Registreringsnummer:", None),
                file_type="docx",
            )
        )
        assert [(s.number, s.title) for s in sections] == [
            ("1", "Primär kontaktperson."),
            ("2", "Meddelanden."),
            ("3", "Registreringens löptid."),
            (None, "Registreringsinformation"),
        ]
        assert "måste ange en kontaktperson." in sections[0].text
        assert sections[1].text.endswith("Välj språk för meddelanden.\n\nSvenska")

    def test_a_heading_run_into_the_paragraph_before_is_split_off(self) -> None:
        # IBM International Passport Advantage-avtal: the layout model joined heading
        # 1.7 to the end of the paragraph before it.
        _, sections = split_sections(
            document(
                heading("1.6 Allmänna riktlinjer"),
                block(
                    "Om godkännande eller samarbete krävs av endera parten skall detta inte "
                    "oskäligen försenas eller innehållas. 1.7 Gällande lagar och geografisk "
                    "omfattning Varje part är ansvarig för att följa lagar och regelverk."
                ),
                heading("1.8 Avtalets upphörande"),
            )
        )
        assert [s.number for s in sections] == ["1.6", "1.7", "1.8"]
        assert sections[0].text.endswith("oskäligen försenas eller innehållas.")
        assert sections[1].text.startswith("1.7 Gällande lagar och geografisk omfattning")

    def test_numbers_after_abbreviations_stay_in_the_paragraph(self) -> None:
        lines = [
            "Arbetstid är vardagar mellan kl. 08.00 och kl. 17.00. Ersättning utgår inte.",
            "Av p. 6.20.8 Allmänna villkor framgår att Ramavtalsleverantören ska ersätta.",
            "Myndigheten kan fatta nytt tilldelningsbeslut. 2. Efter beslutet görs avropet om.",
        ]
        assert [b.text for b in clean_blocks(document(*map(block, lines)))] == lines

    def test_a_title_read_at_the_end_of_the_next_paragraph(self) -> None:
        # IBM: the layout model read "4." alone, then the end of 3.9 with the title last.
        _, sections = split_sections(
            document(
                block("3.8 IBMs Programprenumeration och Support", kind=BlockKind.LIST_ITEM),
                block("IBM tillhandahåller IBM Programprenumeration och Support.", 1),
                block("3.9 Vald Support", kind=BlockKind.LIST_ITEM),
                block("4.", kind=BlockKind.LIST_ITEM),
                block(
                    "Om Kunden gör detta måste Kunden anskaffa ett återinsättande. IBM "
                    "tillhandahåller inga licenser under detta Avtal för Valda program. "
                    "Hårdvarukomponenter"
                ),
                block("En Hårdvarukomponent är en kvalificerad produkt."),
                block("4.1 IBMs tjänster för Hårdvarukomponenter", kind=BlockKind.LIST_ITEM),
            )
        )
        assert [(s.number, s.title) for s in sections] == [
            ("3.8", "IBMs Programprenumeration och Support"),
            ("3.9", "Vald Support"),
            ("4", "Hårdvarukomponenter"),
            ("4.1", "IBMs tjänster för Hårdvarukomponenter"),
        ]
        assert sections[1].text.endswith("under detta Avtal för Valda program.")
        assert sections[2].text.startswith("4. Hårdvarukomponenter\n\nEn Hårdvarukomponent")

    @staticmethod
    def ibm_terms(last_page: int) -> ParsedDocument:
        """IBM's terms: numbered sections, then country terms from page 3 to `last_page`.

        Page 3 repeats the document's title above the part's own heading.
        """
        return document(
            heading("IBM Användningsvillkor - Allmänna villkor", 1),
            heading("Del 1 - IBM-villkor", 1),
            heading("9. Skadeslöshet", 2),
            block("Kunden skall försvara och hålla IBM skadeslöst.", 2),
            heading("10. Upphävande av IBM SaaS och uppsägning", 2),
            block("IBM säger upp Kundens Prenumeration först efter skriftligt meddelande.", 2),
            heading("IBM Användningsvillkor", 3),
            heading("Del 2 - Landsspecifika villkor", 3),
            block(
                "Följande villkor ersätter eller ändrar de villkor som hänvisas till i del 1.", 3
            ),
            heading("NYA ZEELAND", last_page),
            block("The following is added to this Section 2:", last_page),
        )

    def test_an_unnumbered_part_on_new_pages_after_the_numbered_sections(self) -> None:
        _, sections = split_sections(self.ibm_terms(last_page=4))
        assert [(s.number, s.title, s.level) for s in sections] == [
            (None, "Text före första rubriken", 0),
            ("9", "Skadeslöshet", 1),
            ("10", "Upphävande av IBM SaaS och uppsägning", 1),
            # Named after its own heading, not the repeated document title above it.
            (None, "Del 2 - Landsspecifika villkor", 1),
        ]
        assert sections[2].text.endswith("först efter skriftligt meddelande.")
        assert sections[3].path == ("Del 2 - Landsspecifika villkor",)
        assert sections[3].text.startswith("IBM Användningsvillkor\n\nDel 2")
        assert sections[3].text.endswith("added to this Section 2:")

    def test_a_heading_on_the_last_page_stays_in_the_last_section(self) -> None:
        _, sections = split_sections(self.ibm_terms(last_page=3))
        assert [s.number for s in sections] == [None, "9", "10"]
        assert "Del 2 - Landsspecifika villkor" in sections[2].text

    def test_a_part_starts_only_at_a_heading_that_begins_a_page(self) -> None:
        # Kompetensnivåer: "Nivå 2" is a subheading in the middle of page 7.
        _, sections = split_sections(
            document(
                heading("1 Inledning", 6),
                block("Avtalet gäller konsulttjänster.", 6),
                heading("2 Kompetensnivåer", 7),
                block("Konsulten ska ha rätt nivå.", 7),
                heading("Nivå 2", 7),
                block("Konsulten har några års erfarenhet.", 7),
                heading("Nivå 3", 8),
                block("Konsulten har lång erfarenhet.", 8),
            )
        )
        assert [s.number for s in sections] == ["1", "2"]

    def test_a_numbered_list_in_a_word_file_with_headings_is_not_the_outline(self) -> None:
        # "Kontraktstecknande", IT-drift: the documents in order of precedence.
        outline, sections = split_sections(
            document(
                block("Kontraktstecknande", None),
                heading("Kontraktets omfattning", None, 2),
                block(
                    "Om handlingarna innehåller motstridiga uppgifter gäller de i följande", None
                ),
                block("ordning:", None),
                block("1. Skriftliga ändringar och tillägg till säkerhetsskyddsavtal", None, LIST),
                block("2. Säkerhetsskyddsavtal", None, LIST),
                block("3. Kontraktet med bilagor", None, LIST),
                block("Inga andra handlingar än de ovan nämnda ingår i Kontraktet.", None),
                heading("Underskrift", None, 2),
                block("Detta kontrakt har upprättats i två exemplar.", None),
                file_type="docx",
            )
        )
        assert outline is OutlineKind.HEADINGS
        assert [s.title for s in sections] == [
            "Text före första rubriken",
            "Kontraktets omfattning",
            "Underskrift",
        ]
        assert "3. Kontraktet med bilagor" in sections[1].text

    @staticmethod
    def request_template(*contents: str) -> ParsedDocument:
        """An avropsförfrågan template: Word shows "1 Innehåll", Docling does not count it."""
        return document(
            block("1. Innehåll", None, BlockKind.LIST_ITEM),
            *[block(line, None) for line in contents],
            heading("1 Administrativa uppgifter", None, 1),
            heading("1.1 Avropande organisation", None, 2),
            block("Avropande organisation är [Namn].", None),
            heading("1.2 Avropssvarets språk", None, 2),
            block("Avropssvaret ska vara skrivet på svenska.", None),
            file_type="docx",
        )

    def test_a_word_file_takes_the_numbers_its_contents_show(self) -> None:
        _, sections = split_sections(
            self.request_template(
                "2\tAdministrativa uppgifter\t4",
                "2.1\tAvropande organisation\t4",
                "2.2\tAvropssvarets språk\t4",
            )
        )
        numbered = [s for s in sections if s.number]
        assert [(s.number, s.level) for s in numbered] == [("2", 1), ("2.1", 2), ("2.2", 2)]
        assert numbered[1].path == ("2 Administrativa uppgifter", "2.1 Avropande organisation")
        assert numbered[1].text.startswith("2.1 Avropande organisation")

    def test_an_out_of_date_contents_does_not_renumber(self) -> None:
        _, sections = split_sections(
            self.request_template(
                "2\tAdministrativa uppgifter\t4",
                "2.1\tAvropande organisation\t4",
                "2.2\tSpråk\t4",
            )
        )
        assert [s.number for s in sections if s.number] == ["1", "1.1", "1.2"]


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

    def test_a_heading_without_body_gets_no_chunk_when_it_has_subsections(self) -> None:
        parent = self.section("6 Allmänna villkor", number="6", position=0).model_copy(
            update={"parent": None, "level": 1, "path": ("6 Allmänna villkor",)}
        )
        child = self.section("6.1 Allmänt\n\nDessa villkor gäller.", number="6.1", position=1)
        assert [c.section for c in chunk_sections([parent, child], CONTEXT)] == [1]
        # Without its subsection the heading is all there is, so it gets a chunk.
        assert [c.section for c in chunk_sections([parent], CONTEXT)] == [0]

    def test_a_numbered_section_without_subsections_gets_a_chunk(self) -> None:
        # Bilaga 6, Programvaror och tjänster: the whole amendment is this clause.
        clause = "1.1 För närvarande har inga ändringar eller förtydliganden gjorts till bilagorna"
        chunks = chunk_sections([self.section(clause, number="1.1")], CONTEXT)
        assert [c.text for c in chunks] == [clause]

    def test_the_section_of_a_removed_table_of_contents_gets_no_chunk(self) -> None:
        # IT-drift, Allmänna villkor: "6.1 Innehållsförteckning" is followed by the
        # contents lines, which step 3 removes, and then by "6.2 Allmänt".
        contents = self.section("6.1 Innehållsförteckning", number="6.1").model_copy(
            update={"title": "Innehållsförteckning"}
        )
        assert chunk_sections([contents], CONTEXT) == []

    def test_an_unnumbered_heading_without_body_gets_no_chunk(self) -> None:
        # Microsoft's product terms: "Licensmodell" is followed by "Per kärna/CAL".
        assert chunk_sections([self.section("Licensmodell", number=None)], CONTEXT) == []

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
    assert result.contents_missing == []


class TestContentsMissing:
    def test_a_listed_number_without_a_section_is_reported(self) -> None:
        terms = general_terms()
        # The body loses heading 6.21.1: its number is now part of a sentence.
        blocks = [
            block("Enligt 6.21.1 utgår vite.", b.page)
            if b.text == "6.21.1 Ansvar vid Försening"
            else b
            for b in terms.blocks
        ]
        missing = terms.model_copy(update={"blocks": tuple(blocks)})
        assert chunk_document(missing, CONTEXT).contents_missing == ["6.21.1"]

    def test_a_document_without_contents_is_not_checked(self) -> None:
        result = chunk_document(document(heading("1 Inledning"), heading("2 Villkor")), CONTEXT)
        assert result.contents_missing is None


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
