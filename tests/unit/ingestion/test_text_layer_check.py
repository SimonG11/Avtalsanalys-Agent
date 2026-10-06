"""Tests for avtalsagent.ingestion.text_layer_check.

Most lines are from IBM's International Passport Advantage-avtal and from
"Allmänna villkor" for IT-drift 2023 (avropa.se, 2026-10-05). The question
lines follow TendSign's questions-and-answers logs. The numbered lines that are
not sections ("2. föra en förteckning", "3.1 Inget avsnitt 3 finns") and a few
paragraph lines are made up for the tests.
"""

from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas

from avtalsagent.domain.parsed import Block, BlockKind, ParsedDocument, Section
from avtalsagent.ingestion.step3_chunk import OutlineKind
from avtalsagent.ingestion.text_layer_check import (
    LineCheck,
    TextLayerLine,
    check_document,
    check_lines,
    normalise,
    outline_gaps,
    question_lines,
    text_layer,
)

FOOTER = "23.3-5890-2023 IT-drift 2023, område Mindre Sida 1 (4)"


def block(text: str, page: int = 1, kind: BlockKind = BlockKind.TEXT) -> Block:
    return Block(kind=kind, text=text, page=page)


def document(*blocks: Block) -> ParsedDocument:
    return ParsedDocument(sha256="ab" * 32, file_type="pdf", parser="test", pages=(), blocks=blocks)


def section(number: str | None, text: str = "") -> Section:
    return Section(
        position=0,
        number=number,
        title="",
        level=1,
        parent=None,
        path=(),
        page_start=1,
        page_end=1,
        text=text,
    )


def test_normalise_ignores_case_spacing_and_hyphens_at_line_ends() -> None:
    assert normalise("Ramavtals-\nleverantören ska") == normalise("ramavtalsleverantören  SKA")


def test_a_line_in_no_section_is_missing_unless_step_3_removed_it() -> None:
    parsed = document(
        block("6.1 Allmänt", kind=BlockKind.HEADING),
        block("Dessa Allmänna villkor utgör en del av Kammarkollegiets Ramavtal."),
        block(FOOTER, kind=BlockKind.PAGE_FOOTER),
    )
    sections = [section("6.1", "6.1 Allmänt\n\nDessa Allmänna villkor utgör en del av")]
    pages = [
        [
            "Dessa Allmänna villkor utgör en del av",  # in the section
            "Kammarkollegiets Ramavtal.",  # too short to check
            FOOTER,  # a page label, removed by step 3 on purpose
            "Med Arbetsdag avses en i Sverige helgfri måndag",  # lost
        ]
    ]
    assert check_lines(parsed, sections, pages) == LineCheck(
        checked=3,
        removed=1,
        missing=[TextLayerLine(1, "Med Arbetsdag avses en i Sverige helgfri måndag")],
    )


def test_a_numbered_line_between_sections_is_a_gap() -> None:
    sections = [section("1"), section("1.6"), section("1.8"), section("2")]
    pages = [
        [
            "1.7 Gällande lagar och geografisk omfattning",
            "2. föra en förteckning",  # top level: numbered lists start there
            "1.3 Ändringar i avtalet",  # 1.2 is not a section either
            "1.9 2026-08-28 version",  # not a title
        ],
        ["1.7 Gällande lagar och geografisk omfattning", "3.1 Inget avsnitt 3 finns"],
    ]
    assert outline_gaps(sections, pages) == [
        TextLayerLine(1, "1.7 Gällande lagar och geografisk omfattning")
    ]


def test_the_first_number_under_a_section_is_a_gap_too() -> None:
    sections = [section("1"), section("2")]
    assert outline_gaps(sections, [["1.1 Accepterande av villkor"]]) == [
        TextLayerLine(1, "1.1 Accepterande av villkor")
    ]


def test_questions_are_counted_in_the_text_layer() -> None:
    pages = [["12 Publik fråga", "Avses 5.6.3.1 Kvalitetsledningssystem?"], ["Privat fråga"]]
    assert question_lines(pages) == 2


def test_check_document_finds_gaps_in_a_numbered_outline() -> None:
    parsed = document(
        block("1 Allmänt", kind=BlockKind.HEADING),
        block("1.6 Allmänna riktlinjer", kind=BlockKind.HEADING),
        block("Parterna kommer inte att sprida konfidentiell information."),
        block("1.8 Avtalets upphörande", kind=BlockKind.HEADING),
        block("Efter upphörande eller uppsägning gäller villkoren."),
    )
    pages = [["Parterna kommer inte att sprida konfidentiell information.", "1.7 Gällande lagar"]]
    check = check_document(parsed, pages)
    assert check.outline is OutlineKind.NUMBERED
    assert check.lines == LineCheck(checked=1, removed=0, missing=[])
    assert check.gaps == [TextLayerLine(1, "1.7 Gällande lagar")]
    assert (check.questions, check.question_sections) == (None, None)


def test_check_document_counts_the_questions_of_a_log() -> None:
    parsed = document(
        block("Frågor och svar - Upphandlingsdokument"),
        block("1 Publik fråga"),
        block("Avses ett certifierat ledningssystem?"),
        block("2 Publik fråga"),
        block("Ska priset anges utan moms?"),
        block("Privat fråga"),
        block("Frågan besvaras direkt till den som ställt den."),
    )
    pages = [
        ["Frågor och svar - Upphandlingsdokument", "1 Publik fråga", "2 Publik fråga"],
        ["Privat fråga", "Publikt informationsmeddelande"],
    ]
    check = check_document(parsed, pages)
    assert check.outline is OutlineKind.QUESTIONS
    # The text before the first question is a section too, but not a question.
    assert (check.questions, check.question_sections, check.gaps) == (4, 3, [])


def test_text_layer_reads_the_lines_of_each_page(tmp_path: Path) -> None:
    path = tmp_path / "terms.pdf"
    canvas = Canvas(str(path), pagesize=A4)
    canvas.drawString(72, 800, "6.1 Allmänt")
    canvas.drawString(72, 780, "Dessa Allmänna villkor utgör en del av Ramavtalet.")
    canvas.showPage()
    canvas.drawString(72, 800, "6.2 Definitioner")
    canvas.save()
    pages = text_layer(path)
    assert len(pages) == 2
    assert [line.strip() for line in pages[0] if line.strip()] == [
        "6.1 Allmänt",
        "Dessa Allmänna villkor utgör en del av Ramavtalet.",
    ]
    assert "6.2 Definitioner" in [line.strip() for line in pages[1]]
