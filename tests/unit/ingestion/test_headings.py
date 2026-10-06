"""Tests for avtalsagent.ingestion.headings.

The lines are verbatim from avropa.se documents (2026-10-05), mostly from
"Allmänna villkor" for IT-drift 2023 (23.3-5890-2023).
"""

import pytest

from avtalsagent.domain.parsed import Block, BlockKind
from avtalsagent.ingestion.headings import (
    ContentsEntry,
    contents_entries,
    find_candidates,
    infer_numbers,
    listed_numbers,
    parse_number,
    select_outline,
    step_penalty,
    toc_entries,
)


def text(line: str, page: int = 1) -> Block:
    return Block(kind=BlockKind.TEXT, text=line, page=page)


def heading(line: str, page: int = 1) -> Block:
    return Block(kind=BlockKind.HEADING, text=line, page=page)


def numbers(blocks: list[Block]) -> list[str | None]:
    return [h.number for h in select_outline(find_candidates(blocks))]


@pytest.mark.parametrize(
    ("line", "number", "title"),
    [
        (
            "6.21.9 Ramavtalsleverantörens uppsägningsrätt",
            (6, 21, 9),
            "Ramavtalsleverantörens uppsägningsrätt",
        ),
        ("6. Allmänna villkor", (6,), "Allmänna villkor"),
        ("1\tPersonuppgiftsbiträdesavtalets syfte", (1,), "Personuppgiftsbiträdesavtalets syfte"),
        (
            "2.\tKontaktperson för meddelanden och onlineadministration.",
            (2,),
            "Kontaktperson för meddelanden och onlineadministration.",
        ),
        ("1.1 ”Avtalet” avser detta ramavtal", (1, 1), "”Avtalet” avser detta ramavtal"),
        (
            "8.1.76 Överlämning och avslut av Kontrakt",
            (8, 1, 76),
            "Överlämning och avslut av Kontrakt",
        ),
        ("5.1.1 Administratör 1", (5, 1, 1), "Administratör 1"),  # a title ending in a digit
        ("1 PARTER", (1,), "PARTER"),
        # A list number stuck to the title is dropped from it.
        (
            "7.9 1.Åtaganden vid nyttjanderättstidens slut",
            (7, 9),
            "Åtaganden vid nyttjanderättstidens slut",
        ),
    ],
)
def test_numbered_lines_are_candidates(line: str, number: tuple[int, ...], title: str) -> None:
    [candidate] = find_candidates([text(line)])
    assert candidate.number == number
    assert candidate.title == title


@pytest.mark.parametrize(
    "line",
    [
        "2024-09-25",  # a date
        "100 000 kronor per år",  # an amount: the title starts with a digit
        "9241-11 samt kompetens om metoder för användarcentrerad systemutveckling",
        "30 dagar efter fakturadatum",  # a wrapped line: lower-case title
        "2. FN:s barnkonvention (artikel 32),",  # a list item ending with a comma
        "8 X",  # a ticked box, not a title
        "2021000829",  # an organisation number
        # Lines from the survey of all 207 documents (2026-10-05):
        "6. ",  # a list number printed after its item
        "17.00. Såvida inte annat överenskommes i Kontrakt, gäller att servicefönster",  # a time
        "22.00 och kl. 06.00, mellan kl. 19.00 på fredag och kl. 07.00 på måndag samt",
        "23.3-14537-2023 Bemanningstjänster Publicerad 2024-09-24 15:26",  # a footer
        "23.3.2649-22 Programvaror och tjänster -",  # a footer with a dotted case number
        "2.1 eller motsvarande eller hur tillgängligt det som avropas är för personer med",
        "1.0 2024-09-27 Första versionen",  # a row of a version table
        "10(11)",  # a page label
        "500 000 SEK. Utan att någon av punkterna 1-3 föreligger kan Kammarkollegiet",
        "1http://www.opensource.org/licenses/",  # a footnote number
        "11 - 20 Arbetsdagar 26 eller fler Konsulter 10 Arbetsdagar",  # a table row
        "1 mars 2024",
        "(6.10.9 Avsnittet har utgått.)",
        "a. Ditt svar?",
        "7 har träffats ",  # a form field split over lines
    ],
)
def test_lines_that_are_not_headings(line: str) -> None:
    assert find_candidates([text(line)]) == []


def test_a_number_alone_takes_its_title_from_the_next_block() -> None:
    [candidate] = find_candidates([text("6.21.9"), text("Ramavtalsleverantörens uppsägningsrätt")])
    assert candidate.number == (6, 21, 9)
    assert candidate.consumed == 2
    assert candidate.title == "Ramavtalsleverantörens uppsägningsrätt"


def test_parser_headings_score_higher_than_list_items() -> None:
    as_heading, as_text, as_list = (
        find_candidates([Block(kind=kind, text="14 Uppsägning", page=1)])[0].score
        for kind in (BlockKind.HEADING, BlockKind.TEXT, BlockKind.LIST_ITEM)
    )
    assert as_heading > as_text > as_list


@pytest.mark.parametrize(
    ("previous", "current", "penalty"),
    [
        ("6.6", "6.6.1", 0.0),  # first child
        ("6.6.8", "6.6.9", 0.0),  # next sibling
        ("6.6.8", "6.7", 0.0),  # back up one level
        ("6.21.10", "6.22", 0.0),
        ("6.9.2.2", "6.10", 0.0),  # back up two levels
        ("6.2", "6.4", 0.75),  # one number missing
        ("6", "6.1.1", 1.0),  # a level without its own heading
        ("2.5.3", "2.7.1", 1.75),  # one number missing and a level skipped
        ("6.6.8", "6.6.8", None),  # the same number again
        ("6.6.8", "6.6", None),  # a parent after its child
        ("6.6.8", "2", None),  # a numbered list item inside 6.6.8
        ("1.13.3", "1", None),  # numbering never restarts
        ("4", "4.2.3", None),  # skips 4.2.1 and 4.2.2
        ("1.3", "2.4", None),  # skips 2.1-2.3
    ],
)
def test_step_penalty(previous: str, current: str, penalty: float | None) -> None:
    assert step_penalty(parse_number(previous), parse_number(current)) == penalty


def test_a_number_in_the_contents_may_skip_numbers_on_a_new_level() -> None:
    # A template whose sections 2-2.3 were deleted: the contents go from 1.3 to 2.4.
    # Three missing numbers (2.1-2.3) and a level without its own heading (2).
    assert step_penalty((1, 3), (2, 4), listed=True) == 3 * 0.75 + 1.0
    # 4.1, 4.2.1 and 4.2.2 missing, 4.2 without its own heading.
    assert step_penalty((4,), (4, 2, 3), listed=True) == 3 * 0.75 + 1.0


def test_the_outline_follows_the_contents_over_deleted_sections() -> None:
    contents = [
        text("1.3 Hållbarhetsmål ........................................ 3"),
        text("2.4 Särskilda kontraktsvillkor ............................ 4"),
        text("3 Kravkatalog (krav som kan preciseras eller ställas vid avrop) ...... 4"),
    ]
    body = [
        heading("1 Inledning"),
        heading("1.1 Dokumentets syfte"),
        heading("1.2 Definitioner"),
        heading("1.3 Hållbarhetsmål"),
        heading("2.4 Särskilda kontraktsvillkor"),
        heading("2.4.1 Villkor för miljöhänsyn vid fullgörande av ramavtalet"),
        heading("3 Kravkatalog (krav som kan preciseras eller ställas vid avrop)"),
    ]
    listed = listed_numbers(contents)
    assert listed == {(1, 3), (2, 4), (3,)}
    found = [h.number for h in select_outline(find_candidates(body, listed))]
    assert found == ["1", "1.1", "1.2", "1.3", "2.4", "2.4.1", "3"]
    # Without the contents, 2.4 cannot follow 1.3.
    assert numbers(body) == ["1", "1.1", "1.2", "1.3", "3"]


def test_numbers_quoted_from_another_document_do_not_hide_a_listed_section() -> None:
    # A Kammarkollegiet summary of information security requirements: section 5 quotes
    # requirements 4.6.1-4.6.6 from the tender's requirement specification.
    contents = [
        text(f"{number} {title} ........................................ {page}")
        for number, title, page in [
            (4, "Begränsningskriterier", 6),
            (5, "Tekniska krav", 7),
            (6, "Tilldelningskriterier", 8),
        ]
    ]
    quoted = [
        "4.6.1 Autentisering och auktorisering",
        "4.6.2 Skydd mot skadlig kod",
        "4.6.3 Krypterad lagringsmedia",
        "4.6.4 Krypterad datorkommunikation",
        "4.6.5 Säkerhetskopiering",
        "4.6.6 Loggning",
    ]
    body = [
        heading("4 Begränsningskriterier"),
        heading("5 Tekniska krav"),
        text("Ur Anbudsinbjudan, kapitel Kravspecifikation:"),
        *[heading(line) for line in quoted],
        heading("6 Tilldelningskriterier"),
    ]
    listed = listed_numbers(contents)
    found = [h.number for h in select_outline(find_candidates(body, listed))]
    assert found == ["4", "5", "6"]
    # Without the contents the quoted numbers win, since they are more headings.
    assert numbers(body) == ["4", "4.6.1", "4.6.2", "4.6.3", "4.6.4", "4.6.5", "4.6.6", "6"]


def test_the_outline_skips_a_numbered_list_inside_a_section() -> None:
    blocks = [
        heading("1.13 Ersättning till Kammarkollegiet och redovisning av statistik"),
        heading("1.13.3 Redovisning och statistik"),
        text("Ramavtalsleverantören ska redovisa:"),
        text("1 Avropsberättigads ID/referensnummer eller motsvarande"),
        text("2 Avropsberättigad"),
        text("3 Avropsordning (förnyad konkurrensutsättning eller särskild fördelningsnyckel)"),
        heading("1.14 Uppföljning"),
        heading("1.15 Avtalsbrott och påföljder"),
    ]
    assert numbers(blocks) == ["1.13", "1.13.3", "1.14", "1.15"]


def test_the_outline_can_start_above_one() -> None:
    blocks = [
        heading("6. Allmänna villkor"),
        heading("6.1 Innehållsförteckning"),
        heading("6.2 Allmänt"),
        text("Dessa Allmänna villkor utgör en del av Kammarkollegiets Ramavtal."),
        heading("6.3 Definitioner"),
    ]
    assert numbers(blocks) == ["6", "6.1", "6.2", "6.3"]


def test_headings_whose_parents_have_no_number_in_the_text() -> None:
    # "Vägledning IT-konsulttjänster": 2.1-2.5 are headings without numbers.
    blocks = [
        heading("2 IT-konsulttjänster Resurskonsulter och uppdragskonsulter"),
        heading("Avropsberättigade"),
        heading("2.5.1 Delområden"),
        heading("2.5.2 Kompetensområden och exempelroller"),
        heading("2.7.1 Särskild fördelningsnyckel och avropsblankett"),
        heading("3 Om avropet"),
    ]
    assert numbers(blocks) == ["2", "2.5.1", "2.5.2", "2.7.1", "3"]


class TestTableOfContents:
    TOC = [
        heading("Innehåll", page=2),
        text("6. Allmänna villkor 4", page=2),
        text("6.1 Innehållsförteckning 4", page=2),
        text("6.2 Allmänt 4", page=2),
        text("6.3 Definitioner 4", page=2),
    ]
    BODY = [
        heading("6. Allmänna villkor", page=4),
        heading("6.1 Innehållsförteckning", page=4),
        heading("6.2 Allmänt", page=4),
        text("Dessa Allmänna villkor utgör en del av Kammarkollegiets Ramavtal.", page=4),
        heading("6.3 Definitioner", page=4),
    ]

    def test_entries_ending_in_page_numbers_are_found(self) -> None:
        assert toc_entries(self.TOC + self.BODY) == {1, 2, 3, 4}

    def test_entries_are_not_candidates_but_their_numbers_raise_the_body_headings(self) -> None:
        with_toc = find_candidates(self.TOC + self.BODY)
        without_toc = find_candidates(self.BODY)
        assert [c.index for c in with_toc] == [5, 6, 7, 9]
        assert all(a.score > b.score for a, b in zip(with_toc, without_toc, strict=True))

    def test_tab_and_dot_leaders_mark_a_single_entry(self) -> None:
        blocks = [
            text("1\tPersonuppgiftsbiträdesavtalets syfte\t3"),
            text("Mall för redovisning ....................................... 1"),
        ]
        assert toc_entries(blocks) == {0, 1}

    def test_a_single_heading_ending_in_a_number_is_not_an_entry(self) -> None:
        blocks = [
            heading("2.4.4 Arbetsorder för konsult- och supporttjänster Bilaga 12 och Bilaga 13"),
            text("Microsoft erbjuder konsulttjänster enligt Bilaga 12."),
        ]
        assert toc_entries(blocks) == set()
        assert numbers(blocks) == ["2.4.4"]

    def test_a_numbered_list_ending_in_numbers_on_a_later_page_is_not_an_entry(self) -> None:
        # "kompetensnivå 4" on page 11 cannot be a contents entry for page 4.
        items = [
            "1. Lösningsarkitekt, kompetensnivå 4",
            "2. Cybersäkerhetsspecialist, kompetensnivå 4",
            "3. Projektledare, kompetensnivå 4",
        ]
        blocks = [Block(kind=BlockKind.LIST_ITEM, text=item, page=11) for item in items]
        assert toc_entries(blocks) == set()
        # The same lines on page 2, pointing forward, are entries.
        assert toc_entries([b.model_copy(update={"page": 2}) for b in blocks]) == {0, 1, 2}

    def test_a_toc_block_from_the_parser_is_an_entry(self) -> None:
        block = Block(kind=BlockKind.TOC, text="1 Inledning 2\n2 Kravkatalog 3", page=2)
        assert toc_entries([block]) == {0}


def test_no_candidates_give_no_outline() -> None:
    assert select_outline([]) == []


def test_levels_and_titles() -> None:
    [first, second] = select_outline(
        find_candidates(
            [heading("6.21 Avtalsbrott och påföljder"), heading("6.21.1 Ansvar vid Försening")]
        )
    )
    assert (first.number, first.level, first.title) == ("6.21", 2, "Avtalsbrott och påföljder")
    assert (second.number, second.level) == ("6.21.1", 3)


def test_contents_entries_without_leaders_and_page_numbers() -> None:
    blocks = [
        Block(
            kind=BlockKind.TOC,
            text="Exempelroller och kompetensnivåer IT-konsulttjänster\n"
            "Resurskonsulttjänster - Verksamhetens IT-behov | ......................... 1\n"
            "1 Området, kompetensområden och exempelroller ............ | 4\n"
            "2.5.1 | Delområden ........................................ | 7",
            page=2,
        )
    ]
    assert contents_entries(blocks) == [
        ContentsEntry(
            None,
            "Exempelroller och kompetensnivåer IT-konsulttjänster "
            "Resurskonsulttjänster - Verksamhetens IT-behov",
        ),
        ContentsEntry((1,), "Området, kompetensområden och exempelroller"),
        ContentsEntry((2, 5, 1), "Delområden"),
    ]


def test_numbers_are_inferred_only_where_the_neighbours_agree() -> None:
    def entries(*numbers: str | None) -> list[ContentsEntry]:
        return [ContentsEntry(parse_number(n) if n else None, "Titel") for n in numbers]

    # 2.1-2.3 end with 2.3 before 2.3.1; then 1.1-1.2 at the same level.
    assert infer_numbers(entries("1", None, None, "2", None, None, None, "2.3.1")) == [
        (1,),
        (1, 1),
        (1, 2),
        (2,),
        (2, 1),
        (2, 2),
        (2, 3),
        (2, 3, 1),
    ]
    # Three entries cannot end with 2.2 after 2: no numbers.
    assert infer_numbers(entries("2", None, None, None, "2.2.1")) == [
        (2,),
        None,
        None,
        None,
        (2, 2, 1),
    ]
    # Before the first numbered entry (a title page) nothing is inferred.
    assert infer_numbers(entries(None, "1", "1.1")) == [None, (1,), (1, 1)]
    # The chapter number is missing too: counting back passes 4.1 to its parent 4.
    assert infer_numbers(entries("3.7.12", None, None, None, "4.2.1")) == [
        (3, 7, 12),
        (4,),
        (4, 1),
        (4, 2),
        (4, 2, 1),
    ]
