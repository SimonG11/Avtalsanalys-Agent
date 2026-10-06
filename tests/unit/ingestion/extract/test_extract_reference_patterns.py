"""Tests for avtalsagent.ingestion.extract.reference_patterns.

The lines are real, from the sections of the M4 pilot files (avropa.se,
2026-10-05), cited as sha[:12] §section; long lines are shortened at the ends.
Lines marked "made up" are written for the test. No line names a person.
"""

import pytest

from avtalsagent.domain.extracted import (
    DocumentType,
    ReferenceKind,
    ReferenceMention,
    ReferenceStatus,
)
from avtalsagent.domain.parsed import Section
from avtalsagent.ingestion.extract.reference_patterns import find_mentions


def section(text: str, *, title: str = "", position: int = 1) -> Section:
    return Section(
        position=position,
        number=None,
        title=title,
        level=1,
        parent=None,
        path=(),
        page_start=None,
        page_end=None,
        text=text,
    )


def mentions(
    text: str,
    document_type: DocumentType = DocumentType.GENERAL_TERMS,
    *,
    questions_log: bool = False,
    title: str = "",
    position: int = 1,
) -> list[ReferenceMention]:
    found = find_mentions(
        [section(text, title=title, position=position)], document_type, questions_log
    )
    for mention in found:
        assert mention.raw == text[mention.start : mention.end]
    return found


def rules(found: list[ReferenceMention]) -> list[tuple[str, str]]:
    return [(mention.rule, mention.key) for mention in found]


def only(found: list[ReferenceMention], rule: str) -> ReferenceMention:
    [mention] = [mention for mention in found if mention.rule == rule]
    return mention


# --- 1. Laws -------------------------------------------------------------------------------

# 0692da436391 §1.2.2.
LOU = "enligt 1 kap. 18 § och 22 § lagen (2016:1145) om offentlig upphandling (LOU)"


def test_overlapping_law_matches_are_one_external_mention() -> None:
    found = mentions(f"Upphandlingen har genomförts {LOU}.")
    assert [(m.kind, m.status, m.key) for m in found] == [
        (ReferenceKind.LAW, ReferenceStatus.EXTERNAL, "1 kap. 18 § och 22 § lagen (2016:1145)"),
        (ReferenceKind.LAW, ReferenceStatus.EXTERNAL, "LOU"),
    ]


@pytest.mark.parametrize(
    ("text", "law"),
    [
        # 18309f4961d3 §5.16.3: chapter, paragraph and point of a statute.
        ("uteslutas enligt 17 kap. 17 § punkten 3 LOU.", "17 kap. 17 § punkten 3 LOU"),
        # A paragraph alone (made up around a real reference).
        ("Sekretess gäller enligt 21 § andra stycket.", "21 § andra stycket"),
        # 4b6c2a533fae §3.3: an SFS number, merged with the law's names before it.
        (
            "Enligt LOU (Lagen om offentlig upphandling, SFS 2016:1145) ska det göras",
            "LOU (Lagen om offentlig upphandling, SFS 2016:1145",
        ),
        # 54211e718d8e §9.5.1.1: an EU act.
        ("enligt vad som anges i bilaga XI till direktiv 2014/24/EU. För svenska", "2014/24/EU"),
        # 0486216326ec §8.1: articles.
        ("skyldigheterna enligt art. 32-36 och", "art. 32-36"),
        # A law by its name (made up around a real name).
        ("i enlighet med säkerhetsskyddslagen och", "säkerhetsskyddslagen"),
        # 1694a95024a4 §1.1: a standard.
        ("standarder på området, ISO/IEC 27001 respektive", "ISO/IEC 27001"),
    ],
)
def test_each_kind_of_law_is_found(text: str, law: str) -> None:
    assert ("LAW", law) in rules(mentions(text))


def test_a_section_number_in_or_before_a_law_is_part_of_the_law() -> None:
    # 18309f4961d3 §5.16.3 and 20ddb9ebf9cc §42.
    assert rules(mentions("uteslutas enligt 17 kap. 17 § punkten 3 LOU.")) == [
        ("LAW", "17 kap. 17 § punkten 3 LOU")
    ]
    assert rules(mentions("Kapitel 14 i LOU reglerar kvalificeringskrav")) == [("LAW", "LOU")]
    # Made up: the same words before a document.
    assert rules(mentions("Kapitel 14 i Allmänna villkor reglerar")) == [("R1x", "14")]


# --- 2. Placeholders -----------------------------------------------------------------------


def test_template_fields_are_placeholders() -> None:
    # 232f65cf161a §10 and 50edddbad6c7 §354.
    found = mentions("Bilaga nr x\tSäkerhetsskyddsavtal")
    assert [(m.rule, m.kind, m.status) for m in found if m.rule == "PH"] == [
        ("PH", ReferenceKind.ANNEX_NUMBER, ReferenceStatus.PLACEHOLDER)
    ]
    found = mentions("refererar till anbudsinbjudan kapitel 4-8 och avsnitt X.1.1, X.1.2 och X.1.3")
    assert ("PH", "X.1.1") in rules(found)
    assert only(found, "PH").kind is ReferenceKind.SECTION_NUMBER


# --- 3. Section numbers --------------------------------------------------------------------


def test_one_mention_per_number_with_its_own_offsets() -> None:
    # 3a316e27aadf §41.
    text = "som att det avser alla punkterna 5.15.2.1, 5.15.2.2 och 5.15.2.3 och att det"
    found = mentions(text)
    assert [(m.rule, m.raw, m.key, m.status) for m in found] == [
        ("R1", "punkterna 5.15.2.1", "5.15.2.1", None),
        ("R1", "5.15.2.2", "5.15.2.2", None),
        ("R1", "5.15.2.3", "5.15.2.3", None),
    ]


def test_a_range_gives_both_ends() -> None:
    # 0486216326ec §6.3.
    found = mentions("vidta de säkerhetsåtgärder som framgår nedan, punkterna 6.4-6.9.")
    assert rules(found) == [("R1", "6.4"), ("R1", "6.9")]


def test_a_version_table_is_not_a_section_reference() -> None:
    # 4f886a784c5d §pos0.
    text = "Versioner | Publicerat datum | Uppdaterat avsnitt\n1.0 | 2024-09-27 | Första versionen"
    assert rules(mentions(text)) == []


def test_p_after_a_number_is_points() -> None:
    # c59dbfeeb576 §3.3.2.2 ("0 p." is poäng) and 3a316e27aadf §29 ("p." is punkt).
    assert rules(mentions("IT-tjänster IT-tjänster\n\n0 p.\n\n0 p.\n\nborträknat de som")) == []
    assert ("R1x", "6.20.11") in rules(mentions("Fråga kring p. 6.20.11 i Upphandlingsdokument"))


def test_a_bare_number_with_a_dot_after_enligt_is_a_section() -> None:
    # 0486216326ec §4.2; the whole number is made up.
    text = "I samband med revision enligt 10.4 svarar den personuppgiftsansvarige för kostnaden"
    found = mentions(text)
    assert [(m.rule, m.raw, m.key) for m in found] == [("R1", "enligt 10.4", "10.4")]
    assert rules(mentions("I samband med revision enligt 10 svarar den")) == []


def test_a_document_named_after_the_number_is_its_document() -> None:
    # bdf58b81d100 §39: the document is not a mention of its own.
    found = mentions("Fråga avseende p. 6.8.1 i Allmänna villkor. Det framstår som märkligt")
    assert [(m.rule, m.key, m.document_name) for m in found] == [
        ("R1x", "6.8.1", "allmänna villkor")
    ]
    # 50edddbad6c7 §255: "i mallen för".
    found = mentions(
        "förklara innebörden av avsnitt 14.4 i mallen för personuppgiftsbiträdesavtal, i"
    )
    assert [(m.rule, m.document_name) for m in found] == [("R1x", "personuppgiftsbiträdesavtal")]


def test_a_document_named_before_the_number_is_its_document() -> None:
    # 49f36699a469 §2.7 and c58d93eb5c85 §pos11.
    found = mentions("I Allmänna villkor punkt 6.17 finns en valutaklausul angiven")
    assert [(m.rule, m.key, m.document_name) for m in found] == [
        ("R1x", "6.17", "allmänna villkor")
    ]
    found = mentions("Leveranskontroll beskrivs i bilaga allmänna villkor under avsnitt 6.15.")
    assert [(m.rule, m.document_name) for m in found] == [("R1x", "allmänna villkor")]


def test_a_document_on_the_line_before_is_not_named_with_the_number() -> None:
    # 20c753d88340 §106: a heading line, then a new paragraph.
    found = mentions('Ref §13.2 och §13.3 Datadelningsavtal\n\nEnligt 13.2 ska "avgifter" bäras av')
    assert rules(found) == [("R2", "datadelningsavtal"), ("R1", "13.2")]


# 124cb5f66cc3 §6.19.4, shortened: the section numbers its own items.
PENALTIES = (
    "Avropsberättigad rätt till vite enligt nedan.\n\n"
    "1. 5 000 SEK den första gången detta sker under en period om 24 månader.\n\n"
    "2. 7 500 SEK den andra gången detta sker under samma period som i punkten 1."
)


def test_a_whole_number_that_numbers_a_list_item_of_the_section_is_a_list_item() -> None:
    assert [(m.key, m.status) for m in mentions(PENALTIES)] == [("1", ReferenceStatus.LIST_ITEM)]
    # Made up: no such item in the section.
    assert [m.status for m in mentions("samma period som i punkten 1.")] == [None]


def test_a_list_item_may_have_a_letter() -> None:
    # 9a0b5eaeef4b §3.6.3.1, shortened.
    text = (
        "ett strukturerat och dokumenterat kvalitetsledningssystem i enlighet med gällande "
        "utgåva av ISO 9001, eller likvärdigt enligt punkt 1a) eller 2a) nedan.\n\n"
        "1. ett giltigt certifikat för ISO 9001, eller\n\n"
        "1a) ett likvärdigt kvalitetsledningssystem som är certifierat av ett ackrediterat organ"
    )
    found = mentions(text)
    assert [(m.key, m.status) for m in found if m.rule == "R1"] == [
        ("1a", ReferenceStatus.LIST_ITEM)
    ]


def test_a_whole_number_before_ovan_is_a_list_item() -> None:
    # cecf3c2a17cb §5.6.4.
    found = mentions("stämmer överens med standarder som framgår av punkten 2 ovan.")
    assert [m.status for m in found] == [ReferenceStatus.LIST_ITEM]
    # Made up: a section number is not.
    assert [m.status for m in mentions("som framgår av punkten 2.4 ovan.")] == [None]


def test_a_whole_number_after_a_named_section_is_its_list_item() -> None:
    # 34d71a7e4da0 §8.16.4: item 1 of the section "Kammarkollegiets uppsägningsrätt".
    found = mentions(
        "säga upp Kontraktet enligt avsnitt Kammarkollegiets uppsägningsrätt punkten 1."
    )
    assert [(m.rule, m.key, m.status) for m in found] == [
        ("R4", "Kammarkollegiets uppsägningsrätt", None),
        ("R1", "1", ReferenceStatus.LIST_ITEM),
    ]


# --- 4. Section titles ---------------------------------------------------------------------


def test_a_title_runs_to_the_end_of_the_sentence() -> None:
    # 0692da436391 §1.11.1.
    text = "kan det resultera i vite och andra påföljder enligt avsnitt Avtalsbrott och påföljder."
    found = mentions(text)
    assert [(m.rule, m.raw, m.key) for m in found] == [
        ("R4", "avsnitt Avtalsbrott och påföljder", "Avtalsbrott och påföljder")
    ]


def test_a_title_continues_over_a_line_break_before_a_small_letter() -> None:
    # 14aa1cc8ee3d §9.7.5.
    found = mentions("enligt avsnitt Redovisning av\n\nstatistik.\n\nRamavtalsleverantören ska")
    assert rules(found) == [("R4", "Redovisning av statistik")]


def test_a_title_ends_where_the_next_one_starts() -> None:
    # ddf8f04f4ab4 §4.2.2.
    text = (
        "deltagare enligt avsnitt Betalning av skatter och avsnitt Betalning av "
        "socialförsäkringsavgifter och avsnitt Uteslutningsgrunder"
    )
    assert rules(mentions(text)) == [
        ("R4", "Betalning av skatter"),
        ("R4", "Betalning av socialförsäkringsavgifter"),
        ("R4", "Uteslutningsgrunder"),
    ]


def test_a_quoted_title_ends_at_the_quote() -> None:
    # 386122e82b7b §pos1.
    text = 'D. Punkten "Övrigt" under underrubriken "Tvistlösning" i Campus- och School-avtal'
    assert rules(mentions(text)) == [("R4", "Övrigt"), ("R4", "Tvistlösning")]


def test_a_title_starts_with_a_capital() -> None:
    # cecf3c2a17cb §6.16.3 and 54211e718d8e §3.2: the section itself, a direction.
    assert rules(mentions("Uppsägningsrätt enligt detta avsnitt gäller oavsett")) == []
    assert rules(mentions("kommer begränsning göras enligt punkten nedan.")) == []


def test_a_document_that_starts_a_title_is_the_title() -> None:
    # b9881478f6d6 §2.1: "kapitel <Dok>" is not built, so it stays a title.
    found = mentions("Observera att det är utifrån kapitel Kravkatalog som Kund ställer sina krav")
    assert rules(found) == [("R4", "Kravkatalog som Kund ställer sina krav")]


# --- 5. Annex numbers ----------------------------------------------------------------------


def test_annex_numbers() -> None:
    # 171a3cacf5fd §10.1.
    found = mentions("Tillägg och förtydliganden till bilagorna 5.1-5.4 o")
    assert [(m.rule, m.kind, m.key) for m in found] == [
        ("R3", ReferenceKind.ANNEX_NUMBER, "5.1"),
        ("R3", ReferenceKind.ANNEX_NUMBER, "5.4"),
    ]


def test_a_numbered_list_after_bilagor_is_not_an_annex() -> None:
    # 124cb5f66cc3 §6.2.
    text = "1. Huvuddokumentet med bilagor 2. Upphandlingsdokumenten med bilagor inklusive"
    assert [m.rule for m in mentions(text)] == ["R2", "R2"]


# --- 6. Annex names ------------------------------------------------------------------------


def test_an_annex_name_runs_to_the_end_of_the_sentence() -> None:
    # 34d71a7e4da0 §8.10.1.
    found = mentions("Ramavtalsleverantörens priser anges i bilaga Priser.")
    assert [(m.rule, m.raw, m.key, m.document_name) for m in found] == [
        ("R5", "bilaga Priser", "Priser", None)
    ]


def test_an_annex_named_after_a_document_names_it() -> None:
    # 20c753d88340 §pos8: the document is not a mention of its own.
    text = (
        "· Bilaga Utkast till Säkerhetsskyddsavtal (Nivå 1)\n\n"
        "· Bilaga Utkast till Säkerhetsskyddsavtal (Nivå 2)"
    )
    found = mentions(text)
    assert [(m.rule, m.key, m.document_name) for m in found] == [
        ("R5", "Utkast till Säkerhetsskyddsavtal (Nivå 1)", "säkerhetsskyddsavtal"),
        ("R5", "Utkast till Säkerhetsskyddsavtal (Nivå 2)", "säkerhetsskyddsavtal"),
    ]


def test_an_annex_name_ends_where_the_next_annex_starts() -> None:
    # 11db2f3d1852 §4.1.
    text = "I bilaga Konsultkompetens, och därtill kopplade tilldelningskriterier, gäller bilaga"
    assert rules(mentions(text))[0] == (
        "R5",
        "Konsultkompetens, och därtill kopplade tilldelningskriterier, gäller",
    )


def test_a_roman_numeral_is_an_annex_of_an_eu_act() -> None:
    # 54211e718d8e §9.5.1.1.
    found = mentions("enligt vad som anges i bilaga XI till direktiv 2014/24/EU. För svenska")
    assert rules(found) == [("LAW", "2014/24/EU")]


# --- 7. Named documents --------------------------------------------------------------------


def test_a_named_document() -> None:
    # 14aa1cc8ee3d §9.4.2.
    found = mentions("Leverans ska ske i enlighet med Allmänna villkor.")
    assert [(m.rule, m.kind, m.key, m.status, m.topic) for m in found] == [
        ("R2", ReferenceKind.DOCUMENT, "allmänna villkor", None, None)
    ]


def test_a_lower_case_common_noun_is_not_a_document() -> None:
    # 1d58dc2e8387 §4 and 49f36699a469 §1.1.
    assert rules(mentions("För mer vägledning, se på Avropa, kontakta förvaltaren")) == []
    assert rules(mentions("Vägledningen riktar sig i första hand till avropande")) == [
        ("R2", "vägledning")
    ]


def test_the_document_label_on_a_tendsign_cover_is_not_a_reference() -> None:
    # c8aecf2b554c §pos0.
    text = "Upphandlingsdokument\n\n2022-06-14\n\nUpphandlande organisation"
    assert rules(mentions(text, position=0)) == []
    assert rules(mentions(text, position=3)) == [("R2", "upphandlingsdokument")]


def test_this_document_is_self() -> None:
    # 54211e718d8e §11.1.
    found = mentions(
        "I dessa Allmänna villkor framgår vilka punkter som kan preciseras i Kontrakt."
    )
    assert [m.status for m in found] == [ReferenceStatus.SELF]


def test_a_definition_as_this_document_is_self() -> None:
    # 0692da436391 §1.3: the term in the first cell is a mention, the definition is SELF.
    text = "Huvuddokument | Med Huvuddokument avses detta dokument som innehåller krav och villkor"
    assert [m.status for m in mentions(text)] == [None, ReferenceStatus.SELF]
    # 11db2f3d1852 §6.3: defined as another agreement.
    text = "Med Personuppgiftsbiträdesavtal avses ett avtal mellan Kund och den som behandlar"
    assert [m.status for m in mentions(text)] == [None]


def test_a_topic_after_a_document_ends_before_a_relative_clause() -> None:
    # 0692da436391 §1.10.2.
    text = (
        "villkor i Allmänna villkor gällande viten, skadestånd och ersättningar som är "
        "kostnadsdrivande för Ramavtalsleverantören."
    )
    assert only(mentions(text), "R2").topic == "viten, skadestånd och ersättningar"
    # 11db2f3d1852 §5.10.6: ends at the sentence.
    text = "av och bestämmelser i tecknat Personuppgiftsbiträdesavtal avseende radering."
    assert only(mentions(text), "R2").topic == "radering"


def test_om_and_a_capital_do_not_start_a_topic() -> None:
    # 07629941f3e6 §9: "om" means "if"; bea056be2d79 §pos2 (without "allmänna"): a name.
    text = "som kommer med anledning av detta Säkerhetsskyddsavtal om inte något annat avtalas."
    assert only(mentions(text), "R2").topic is None
    text = "eventuella motstridande villkor i Enterprise-avtalet avseende Produkter som licensieras"
    assert only(mentions(text), "R2").topic is None


# --- 8. Questions --------------------------------------------------------------------------

# 39d8c1efe373 §7.
FOLLOW_UP = "Följdfråga på fråga 1 avseende 5.6.3.1 Kvalitetsledningssystem"


def test_a_question_number_is_read_only_in_a_questions_log() -> None:
    log = mentions(FOLLOW_UP, DocumentType.QUESTIONS_AND_ANSWERS, questions_log=True)
    assert [(m.rule, m.kind, m.key) for m in log] == [("RQ", ReferenceKind.QUESTION, "1")]
    assert mentions(FOLLOW_UP, DocumentType.QUESTIONS_AND_ANSWERS) == []


def test_the_heading_line_names_the_section_itself() -> None:
    # 20c753d88340 §48: the heading "Publik fråga 48" is not a reference to question 48.
    text = "Publik fråga 48\n\nFrån:\n\nDold\n\nKlargörande gällande fråga 17, menar ni att"
    found = mentions(
        text, DocumentType.QUESTIONS_AND_ANSWERS, questions_log=True, title="Publik fråga"
    )
    assert rules(found) == [("RQ", "17")]


# --- Replacement ---------------------------------------------------------------------------

# 386122e82b7b §pos1, an amendment of Microsoft's Campus and School agreement.
AMENDING = (
    'E. Punkten "Övrigt" under underrubriken "Tillämplig lag" i Campus- och School-avtal '
    "ändras härmed enligt följande: Svensk lag är tillämplig."
)


def test_a_replacement_in_an_amendment_replaces() -> None:
    found = mentions(AMENDING, DocumentType.AMENDMENT)
    assert [(m.key, m.replaces) for m in found] == [("Övrigt", True), ("Tillämplig lag", True)]
    # The same words in a document that is not an amendment do not change anything.
    assert not any(m.replaces for m in mentions(AMENDING, DocumentType.GENERAL_TERMS))


def test_a_replacement_in_a_questions_log_replaces_only_in_the_answer() -> None:
    # 3a316e27aadf §29 (the question, a proposal) and 20ddb9ebf9cc §49 (the answer).
    text = (
        "Anbudsgivaren föreslår att punkt 6.20.6 stryks.\n\nPublikt svar\n\n"
        'I avsnitt 6.8.3 ersätts meningen "Om Ramavtalsleverantören inte har åtgärdat".\n\n'
        "Avsnitt 6.9 gäller oförändrat."
    )
    found = mentions(text, DocumentType.QUESTIONS_AND_ANSWERS, questions_log=True)
    assert [(m.key, m.replaces) for m in found] == [
        ("6.20.6", False),
        ("6.8.3", True),
        ("6.9", False),
    ]


# --- Everything together -------------------------------------------------------------------


def test_mentions_are_ordered_by_section_and_offset() -> None:
    sections = [
        section("enligt avsnitt Avtalsbrott och påföljder och punkt 6.21", position=0),
        section("se bilaga Priser och Allmänna villkor punkt 6.17", position=1),
    ]
    found = find_mentions(sections, DocumentType.MAIN_DOCUMENT, questions_log=False)
    assert [(m.section, m.rule, m.key) for m in found] == [
        (0, "R4", "Avtalsbrott och påföljder"),
        (0, "R1", "6.21"),
        (1, "R5", "Priser"),
        (1, "R1x", "6.17"),
    ]
    assert [(m.section, m.start) for m in found] == sorted((m.section, m.start) for m in found)
