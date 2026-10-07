"""Tests for avtalsagent.validation.latest_wording and its place in the chain.

The rule's tests build a draft as the model would hand it in, check its
quotes against the sections a reader would give (`check_citations`, so a
source is verified as in the chain), and give the rule the amendments a
`find_amendments` reader would give for each passed section. The cases are
the pilot's amendment questions in gold_sv.jsonl: q21 (a questions log
corrects punkt 3.2 of the tender documents), q22 (an amendment puts Swedish
law in Microsoft's agreement) and q23 (an amendment whose reference has
four candidates). The chain's tests run `check_answer` with the doubles of
tests/unit/agent/scripted_model.py. No test needs a model or a database.
"""

from collections.abc import Iterable, Mapping
from datetime import date
from typing import Any

import pytest

from avtalsagent.agent.amendments import AmendmentInfo
from avtalsagent.agent.register_reader import RegisterEntry
from avtalsagent.agent.schemas import DraftCitation, FinalAnswer
from avtalsagent.agent.sections import CitedSection
from avtalsagent.validation.chain import NOT_REVIEWED_NOTE, ChainReport, check_answer
from avtalsagent.validation.citations import check_citations
from avtalsagent.validation.latest_wording import (
    MAX_NAMED,
    WordingReport,
    check_latest_wording,
)
from avtalsagent.validation.review import ReviewInput, ReviewVerdict
from tests.unit.agent.scripted_model import (
    DictAmendments,
    DictReader,
    ListRegister,
    ScriptedReviewer,
)

TODAY = date(2026, 10, 7)


def section(
    sha256: str, position: int, number: str | None, title: str, file_title: str, text: str
) -> CitedSection:
    return CitedSection(
        sha256=sha256,
        section_position=position,
        section_number=number,
        section_title=title,
        file_title=file_title,
        page_titles=["IT-drift Mindre, upp till 200 anställda"],
        page_start=1,
        text=text,
    )


def change(
    amending: CitedSection,
    amended: int | None,
    dated: date | None,
    status: str = "resolved",
) -> AmendmentInfo:
    """`amending` changes the section at `amended` (None: the whole file), as the tool says."""
    return AmendmentInfo(
        sha256=amending.sha256,
        section_position=amending.section_position,
        file_title=amending.file_title,
        section_number=amending.section_number,
        section_title=amending.section_title,
        amended_position=amended,
        status=status,
        dated=dated,
    )


# q21: punkt 3.2 of the tender documents for IT-drift Mindre, and the correction in the
# questions log the day after the question.
TENDER = section(
    "e394ae5dfd2c1a21c78ff55d656af692ed6316aceb3ae0ffe89a92b8cd368edc",
    30,
    "3.2",
    "Utvärderingsmodell",
    "Upphandlingsdokument",
    "De sju (7) anbuden med de högsta jämförelsesummorna är de ekonomiskt mest fördelaktiga "
    "anbuden.",
)
TENDER_QUOTE = "De sju (7) anbuden med de högsta jämförelsesummorna"
CORRECTION = section(
    "7a765d649e25f3cde1f8755e44a41ee9206c102bbbb4036549912eb58ed6769f",
    6,
    "9",
    "Publik fråga",
    "Frågor och svar - Upphandlingsdokument",
    "Publikt svar 2024-02-20 08:39 Rättelse. Texten som gäller är följande för punkt 3.2: "
    '"De åtta (8) anbuden med de högsta jämförelsesummorna är de ekonomiskt mest fördelaktiga '
    'anbuden".',
)
CORRECTION_QUOTE = "De åtta (8) anbuden med de högsta jämförelsesummorna"
CORRECTS = change(CORRECTION, TENDER.section_position, date(2024, 2, 20))

# q22: punkt 10 Övrigt of Microsoft's agreement (Bilaga 4.1), and Bilaga 4's change to it.
MBSA = section(
    "005469cd3990a4ba2cb1ad4ea360c81689e8c302aa94abce5ba858a37ef72c84",
    10,
    "10",
    "Övrigt.",
    "Bilaga 4.1 Microsoft Business and Services Agreement",
    "g. Tillämplig lag. Avtalet regleras av lagstiftningen i Irland.",
)
MBSA_QUOTE = "Avtalet regleras av lagstiftningen i Irland."
SWEDISH_LAW = section(
    "d54ed0900be5ff3923c8943c37223dbf35a0b4aae273cd82513220f60e806f49",
    6,
    None,
    "Amendment ID CTM",
    "Bilaga 4 Tillägg och förtydliganden till bilaga 4.1",
    "Svensk lag - dock ej svenska lagvalsregler - är tillämpliga på detta avtal och "
    "Kompletterande avtal.",
)
SWEDISH_LAW_QUOTE = "Svensk lag - dock ej svenska lagvalsregler - är tillämpliga"
AMENDS_MBSA = change(SWEDISH_LAW, MBSA.section_position, None)  # undated here

# q23: punkt 2 of the Enterprise registration (Bilaga 5.2), and Bilaga 5, whose "Punkt 2a"
# has four candidates: annexes 5.1-5.4 each have a section 2.
REGISTRATION = section(
    "aba1280d90beb8aa3b7849c96d02d142f41072fb49937027487bfdf6a3d54dbb",
    1,
    "2",
    "Beställningskrav.",
    "Bilaga 5.2 Enterprise-registrering",
    "a. Minsta beställningskrav. Det Anslutna Koncernbolagets Organisation måste ha minst 500 "
    "Kvalificerade Användare eller Kvalificerade Enheter.",
)
REGISTRATION_QUOTE = "måste ha minst 500 Kvalificerade Användare"
MINIMUM = section(
    "5aab54c5a4b1bfe36461889bcaa2cc31004116ab0b6774ce2321a419bfc42e94",
    0,
    None,
    "Text före första rubriken",
    "Bilaga 5 Tillägg och förtydligande till bilagorna 5.1-5.4",
    "Minsta beställningskrav. Det Anslutna Koncernbolagets Organisation måste ha minst 100 "
    "Kvalificerade Användare eller Kvalificerade Enheter.",
)

SECTIONS = {
    (s.sha256, s.section_position): s
    for s in [TENDER, CORRECTION, MBSA, SWEDISH_LAW, REGISTRATION, MINIMUM]
}
Changes = Mapping[tuple[str, int], list[AmendmentInfo] | None]


def cite(id: int, cited: CitedSection, quote: str) -> dict[str, Any]:
    return {
        "id": id,
        "sha256": cited.sha256,
        "section_position": cited.section_position,
        "quote": quote,
    }


def draft_of(text: str, *sources: dict[str, Any]) -> FinalAnswer:
    return FinalAnswer(
        answered=True, text=text, citations=[DraftCitation(**source) for source in sources]
    )


def check(text: str, *sources: dict[str, Any], changes: Changes) -> WordingReport:
    """The rule on a draft whose quotes were checked as the chain checks them."""
    draft = draft_of(text, *sources)
    return check_latest_wording(draft, check_citations(draft, SECTIONS).citations, changes)


def key(cited: CitedSection) -> tuple[str, int]:
    return (cited.sha256, cited.section_position)


PASSED = WordingReport(problems=[], reservations=[], unread=False)


# --- the gold cases ---------------------------------------------------------------------------


def test_q21_the_corrected_tender_text_cited_alone_fails() -> None:
    report = check(
        "Sju anbud antas [1].",
        cite(1, TENDER, TENDER_QUOTE),
        changes={key(TENDER): [CORRECTS]},
    )

    assert report.problems == [
        "Källa [1] (Upphandlingsdokument, avsnitt 3.2 Utvärderingsmodell) har ändrats av Frågor "
        "och svar - Upphandlingsdokument, avsnitt 9 Publik fråga (2024-02-20, sha256 "
        f"{CORRECTION.sha256}, section_position 6). Läs ändringen med read_section, bygg svaret "
        "på den senaste lydelsen och citera ändringen."
    ]
    assert report.reservations == [
        "Källa [1] kan bygga på en lydelse som har ändrats senare: Frågor och svar - "
        "Upphandlingsdokument, avsnitt 9 Publik fråga (2024-02-20)."
    ]
    assert not report.unread


def test_q21_citing_the_correction_too_passes() -> None:
    report = check(
        "Punkt 3.2 anger sju [1], men rättelsen säger åtta [2].",
        cite(1, TENDER, TENDER_QUOTE),
        cite(2, CORRECTION, CORRECTION_QUOTE),
        changes={key(TENDER): [CORRECTS], key(CORRECTION): []},
    )

    assert report == PASSED


def test_q22_microsofts_clause_cited_alone_fails_and_names_the_undated_amendment() -> None:
    report = check(
        "Irländsk lag gäller [1].",
        cite(1, MBSA, MBSA_QUOTE),
        changes={key(MBSA): [AMENDS_MBSA]},
    )

    assert report.problems == [
        "Källa [1] (Bilaga 4.1 Microsoft Business and Services Agreement, avsnitt 10 Övrigt.) "
        "har ändrats av Bilaga 4 Tillägg och förtydliganden till bilaga 4.1, avsnittet "
        f"”Amendment ID CTM” (sha256 {SWEDISH_LAW.sha256}, section_position 6). Läs ändringen "
        "med read_section, bygg svaret på den senaste lydelsen och citera ändringen."
    ]
    assert report.reservations == [
        "Källa [1] kan bygga på en lydelse som har ändrats senare: Bilaga 4 Tillägg och "
        "förtydliganden till bilaga 4.1, avsnittet ”Amendment ID CTM”."
    ]


def test_q22_citing_the_amendment_too_passes() -> None:
    report = check(
        "Bilaga 4.1 anger irländsk lag [1], men Bilaga 4 ändrar det till svensk lag [2].",
        cite(1, MBSA, MBSA_QUOTE),
        cite(2, SWEDISH_LAW, SWEDISH_LAW_QUOTE),
        changes={key(MBSA): [AMENDS_MBSA], key(SWEDISH_LAW): []},
    )

    assert report == PASSED


def test_q23_an_ambiguous_amendment_is_left_to_the_agent() -> None:
    report = check(
        "Kravet är 500 användare [1].",
        cite(1, REGISTRATION, REGISTRATION_QUOTE),
        changes={key(REGISTRATION): [change(MINIMUM, 1, None, status="ambiguous")]},
    )

    assert report == PASSED


# --- what the rule checks ---------------------------------------------------------------------


def test_a_section_without_amendments_passes() -> None:
    report = check("Sju anbud antas [1].", cite(1, TENDER, TENDER_QUOTE), changes={key(TENDER): []})

    assert report == PASSED


def test_a_change_to_the_whole_file_or_another_section_is_not_checked() -> None:
    report = check(
        "Sju anbud antas [1].",
        cite(1, TENDER, TENDER_QUOTE),
        changes={key(TENDER): [change(CORRECTION, None, None), change(MINIMUM, 31, None)]},
    )

    assert report == PASSED


@pytest.mark.parametrize("changes", [{key(TENDER): None}, {}], ids=["None", "not read"])
def test_amendments_that_could_not_be_read_give_a_note_and_no_problem(changes: Changes) -> None:
    report = check("Sju anbud antas [1].", cite(1, TENDER, TENDER_QUOTE), changes=changes)

    assert report == WordingReport(
        problems=[],
        reservations=["Det gick inte att kontrollera om källa [1] har ändrats."],
        unread=True,
    )


def test_one_note_names_every_source_whose_amendments_could_not_be_read() -> None:
    report = check(
        "Sju [1], irländsk lag [2], 500 användare [3].",
        cite(1, TENDER, TENDER_QUOTE),
        cite(2, MBSA, MBSA_QUOTE),
        cite(3, REGISTRATION, REGISTRATION_QUOTE),
        changes={key(TENDER): None, key(MBSA): [], key(REGISTRATION): None},
    )

    assert report.reservations == [
        "Det gick inte att kontrollera om källorna [1], [3] har ändrats."
    ]


def test_an_unverified_citation_is_not_checked() -> None:
    report = check(
        "Sex anbud antas [1].",
        cite(1, TENDER, "De sex (6) anbuden med de högsta jämförelsesummorna"),
        changes={key(TENDER): [CORRECTS]},
    )

    assert report == PASSED  # the citation rule names it


def test_an_amendment_whose_quote_failed_does_not_count_as_cited() -> None:
    report = check(
        "Sju [1], men åtta [2].",
        cite(1, TENDER, TENDER_QUOTE),
        cite(2, CORRECTION, "De nio (9) anbuden med de högsta jämförelsesummorna"),
        changes={key(TENDER): [CORRECTS]},
    )

    assert [problem[:9] for problem in report.problems] == ["Källa [1]"]


def test_a_section_cited_twice_gets_one_problem_by_its_first_source() -> None:
    report = check(
        "Sju [1], de högsta [2].",
        cite(1, TENDER, TENDER_QUOTE),
        cite(2, TENDER, "anbuden med de högsta jämförelsesummorna"),
        changes={key(TENDER): [CORRECTS]},
    )

    assert [problem[:9] for problem in report.problems] == ["Källa [1]"]
    assert len(report.reservations) == 1


def test_a_cited_amendment_that_is_amended_in_turn_needs_its_own_amendment_cited() -> None:
    later = change(SWEDISH_LAW, CORRECTION.section_position, date(2024, 3, 1))
    changes = {key(TENDER): [CORRECTS], key(CORRECTION): [later], key(SWEDISH_LAW): []}
    sources = [cite(1, TENDER, TENDER_QUOTE), cite(2, CORRECTION, CORRECTION_QUOTE)]

    amended = check("Sju [1], åtta [2].", *sources, changes=changes)
    cited = check(
        "Sju [1], åtta [2], svensk lag [3].",
        *sources,
        cite(3, SWEDISH_LAW, SWEDISH_LAW_QUOTE),
        changes=changes,
    )

    assert [problem[:9] for problem in amended.problems] == ["Källa [2]"]
    assert cited == PASSED


def test_any_one_amendment_cited_will_do() -> None:
    # Not necessarily the newest: which wording applies is the reviewer's judgement.
    newer = change(SWEDISH_LAW, TENDER.section_position, date(2025, 1, 1))
    report = check(
        "Sju [1], åtta [2].",
        cite(1, TENDER, TENDER_QUOTE),
        cite(2, CORRECTION, CORRECTION_QUOTE),
        changes={key(TENDER): [newer, CORRECTS], key(CORRECTION): []},
    )

    assert report == PASSED


def test_the_problem_names_at_most_three_amendments_newest_first() -> None:
    # Five changes to punkt 3.2, made up but for the correction: only the order matters.
    by_date = [
        (MINIMUM, None),
        (CORRECTION, date(2024, 2, 20)),
        (SWEDISH_LAW, date(2025, 1, 1)),
        (MBSA, date(2023, 5, 1)),
        (REGISTRATION, date(2024, 6, 3)),
    ]
    changes = [change(amending, TENDER.section_position, day) for amending, day in by_date]
    report = check(
        "Sju anbud antas [1].",
        cite(1, TENDER, TENDER_QUOTE),
        changes={key(TENDER): [*changes, changes[1]]},  # one listed twice is named once
    )

    [problem] = report.problems
    named = problem.split(" har ändrats av ", 1)[1].split(". Läs ", 1)[0].split("; ")
    assert MAX_NAMED == 3
    assert [entry.split(",", 1)[0] for entry in named] == [
        SWEDISH_LAW.file_title,
        REGISTRATION.file_title,
        CORRECTION.file_title,
    ]
    assert named[-1].endswith(" och 2 till")
    assert "Läs ändringarna med read_section" in problem
    assert "citera den ändring som gäller." in problem
    [note] = report.reservations
    assert note.endswith(
        "Frågor och svar - Upphandlingsdokument, avsnitt 9 Publik fråga (2024-02-20) och 2 till."
    )


# --- in the chain -----------------------------------------------------------------------------

# Nordlo Advance's agreement on IT-drift Mindre, as the public register has it (gold q10).
NORDLO = RegisterEntry(
    agreement_number="23.3-5890-2023-002",
    procurement_number="23.3-5890-2023",
    supplier_name="Nordlo Advance AB",
    org_number="556486-1689",
    former_names=["EPM Data"],
    framework_area="IT-drift",
    sub_area="IT-drift / IT-drift Mindre, upp till 200 anställda",
    valid_from=date(2024, 11, 14),
    valid_to=date(2028, 11, 13),
    max_extension_to=None,
)
Q21_ANSWER = (
    "Punkt 3.2 angav sju anbud [1], men rättelsen säger åtta [2]. Nordlo Advance AB har avtal "
    "23.3-5890-2023-002."
)
Q21_SOURCES = [cite(1, TENDER, TENDER_QUOTE), cite(2, CORRECTION, CORRECTION_QUOTE)]


class LoggedSections(DictReader):
    def __init__(self, log: list[str], sections: Iterable[CitedSection]) -> None:
        super().__init__(sections)
        self.log = log

    async def read(self, sha256: str, section_position: int) -> CitedSection | None:
        self.log.append(f"read_section {sha256[:12]} {section_position}")
        return await super().read(sha256, section_position)


class LoggedRegister(ListRegister):
    def __init__(self, log: list[str], entries: Iterable[RegisterEntry]) -> None:
        super().__init__(entries)
        self.log = log

    async def read(self, agreement_number: str) -> list[RegisterEntry] | None:
        self.log.append(f"search_register {agreement_number}")
        return await super().read(agreement_number)


class LoggedAmendments(DictAmendments):
    def __init__(self, log: list[str], amendments: Changes) -> None:
        super().__init__(amendments)
        self.log = log

    async def read(self, sha256: str, section_position: int) -> list[AmendmentInfo] | None:
        self.log.append(f"find_amendments {sha256[:12]} {section_position}")
        return await super().read(sha256, section_position)


class LoggedReviewer(ScriptedReviewer):
    def __init__(self, log: list[str]) -> None:
        super().__init__()
        self.log = log

    async def review(self, request: ReviewInput) -> ReviewVerdict | None:
        self.log.append("review")
        return await super().review(request)


async def run_chain(
    draft: FinalAnswer, amendments: DictAmendments, reviewer: ScriptedReviewer | None = None
) -> ChainReport:
    return await check_answer(
        draft,
        sections=DictReader(SECTIONS.values()),
        register=ListRegister([NORDLO]),
        amendments=amendments,
        reviewer=reviewer or ScriptedReviewer(),
        question="Hur många anbud skulle enligt utvärderingsmodellen antas?",
        follow_ups=[],
        today=TODAY,
    )


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_the_chain_reads_the_amendments_after_the_register_and_before_the_review() -> None:
    log: list[str] = []
    draft = FinalAnswer(
        answered=True,
        text=Q21_ANSWER,
        citations=[DraftCitation(**source) for source in Q21_SOURCES],
        register_facts=["23.3-5890-2023-002"],
    )

    report = await check_answer(
        draft,
        sections=LoggedSections(log, SECTIONS.values()),
        register=LoggedRegister(log, [NORDLO]),
        amendments=LoggedAmendments(log, {key(TENDER): [CORRECTS]}),
        reviewer=LoggedReviewer(log),
        question="Hur många anbud skulle enligt utvärderingsmodellen antas?",
        follow_ups=[],
        today=TODAY,
    )

    assert (report.problems, report.reservations, report.amendments_unread) == ([], [], False)
    assert log == [
        "read_section e394ae5dfd2c 30",
        "read_section 7a765d649e25 6",
        "search_register 23.3-5890-2023-002",
        "find_amendments e394ae5dfd2c 30",
        "find_amendments 7a765d649e25 6",
        "review",
    ]


@pytest.mark.anyio
async def test_a_draft_on_a_changed_wording_is_sent_back_without_a_review() -> None:
    reviewer = ScriptedReviewer()
    draft = draft_of("Sju anbud antas [1].", cite(1, TENDER, TENDER_QUOTE))

    report = await run_chain(draft, DictAmendments({key(TENDER): [CORRECTS]}), reviewer)

    [problem] = report.problems
    assert problem.startswith("Källa [1] (Upphandlingsdokument, avsnitt 3.2 Utvärderingsmodell)")
    assert report.reservations == [
        "Källa [1] kan bygga på en lydelse som har ändrats senare: Frågor och svar - "
        "Upphandlingsdokument, avsnitt 9 Publik fråga (2024-02-20).",
        NOT_REVIEWED_NOTE,
    ]
    assert [c.verified for c in report.citations] == [True]
    assert reviewer.requests == []


@pytest.mark.anyio
async def test_unread_amendments_give_a_note_and_the_review_still_runs() -> None:
    reviewer = ScriptedReviewer()
    draft = draft_of("Sju anbud antas [1].", cite(1, TENDER, TENDER_QUOTE))

    report = await run_chain(draft, DictAmendments({key(TENDER): None}), reviewer)

    assert report.problems == []
    assert report.reservations == ["Det gick inte att kontrollera om källa [1] har ändrats."]
    assert report.amendments_unread
    assert len(reviewer.requests) == 1


@pytest.mark.anyio
async def test_the_chain_reads_each_passed_section_once_and_no_failed_one() -> None:
    amendments = DictAmendments({key(TENDER): []})
    draft = draft_of(
        "Sju [1], de högsta [2], 500 användare [3].",
        cite(1, TENDER, TENDER_QUOTE),
        cite(2, TENDER, "anbuden med de högsta jämförelsesummorna"),
        cite(3, REGISTRATION, "måste ha minst 250 Kvalificerade Användare"),
    )

    report = await run_chain(draft, amendments)

    assert [c.verified for c in report.citations] == [True, True, False]
    assert amendments.reads == [key(TENDER)]
    assert not report.amendments_unread
