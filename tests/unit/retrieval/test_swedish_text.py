"""Tests for avtalsagent.retrieval.swedish_text.

The sentences are verbatim from the pilot's parsed documents (data/parsed,
2026-10-06), cited by the file's sha256[:12] and page.
"""

import unicodedata
from importlib.metadata import version

from avtalsagent.retrieval import swedish_text
from avtalsagent.retrieval.swedish_text import (
    ANALYSER,
    STOP_WORDS,
    query_terms,
    terms,
    words,
)

# 185872a6bb90 page 3: the agreement's parties.
PARTIES = (
    "Ramavtal med avtalsnummer 23.3.2649-22-003, har träffats för Avropsberättigades "
    "räkning, mellan Statens inköpscentral vid Kammarkollegiet, organisationsnummer "
    "202100-0829, nedan Kammarkollegiet, och Crayon AB, organisationsnummer 556635-9799, "
    "nedan Ramavtalsleverantören."
)
# 4b6c2a533fae page 6.
VALIDITY = (
    "- Ramavtalet för område 1 - Verksamhetens IT-behov är giltigt från och med "
    "2025-08-19 till och med 2029-08-18."
)
# 18309f4961d3 page 26.
IT_DRIFT = (
    "Ramavtalet IT-drift ska användas när Avropsberättigads behov omfattar tjänster ur "
    "minst två av dessa block."
)
# 087f9c5a2f56 page 3.
SLASH = (
    "Vi tolkar det som att om angiven konsult har slutat hos anbudsgivaren/underleverantör "
    "efter referensuppdraget är slutfört så är detta fortfarande accepterat."
)
# 20c753d88340 page 17.
SKALL = (
    "Fråga 1: I scenariot konsult är anställd hos underleverantör vem hos "
    "underleverantören skall då skriva på"
)
# 0a5491b1398e page 27: a heading.
HEADING = "6.21.9 Ramavtalsleverantörens uppsägningsrätt"


def test_identifiers_are_kept_whole() -> None:
    tokens = words(PARTIES)

    assert "23.3.2649-22-003" in tokens  # agreement number, dotted form
    assert "202100-0829" in tokens  # Kammarkollegiet's organisation number
    assert "556635-9799" in tokens
    # The identifiers are not stemmed or split in terms() either.
    assert {"23.3.2649-22-003", "202100-0829", "556635-9799"} <= set(terms(PARTIES))


def test_case_and_agreement_number_forms() -> None:
    # The register's and the documents' forms; 65d611d12eab page 2 has the first,
    # fb9447f0b8bf page 1 the letterhead form.
    text = "23.3-8321-2024-001 23.3-2940-20:033 23.3-4613-2023-003-A 23.3-5890-2023 96-15-2015"

    assert words(text) == [
        "23.3-8321-2024-001",
        "23.3-2940-20:033",
        "23.3-4613-2023-003-a",
        "23.3-5890-2023",
        "96-15-2015",
    ]


def test_dates_are_kept_whole() -> None:
    assert words(VALIDITY)[-5:] == ["2025-08-19", "till", "och", "med", "2029-08-18"]
    assert terms(VALIDITY)[-2:] == ["2025-08-19", "2029-08-18"]


def test_section_numbers_are_kept_whole() -> None:
    assert words(HEADING) == ["6.21.9", "ramavtalsleverantörens", "uppsägningsrätt"]
    assert terms(HEADING)[0] == "6.21.9"


def test_a_range_of_section_numbers_is_two_numbers() -> None:
    # Not a case number: the series 23.x with a two-digit "serial" and a dot.
    assert words("punkterna 23.1-23.12") == ["punkterna", "23.1", "23.12"]


def test_an_identifier_does_not_swallow_part_of_a_longer_number() -> None:
    assert words("556635-97991") == ["556635", "97991"]
    assert words("2025-08-190") == ["2025", "08", "190"]


def test_hyphens_split_words() -> None:
    tokens = words(IT_DRIFT)

    assert tokens[:3] == ["ramavtalet", "it", "drift"]
    assert "it-drift" not in tokens
    assert words(VALIDITY)[5:7] == ["it", "behov"]


def test_slashes_split_words() -> None:
    tokens = words(SLASH)

    assert tokens[tokens.index("anbudsgivaren") + 1] == "underleverantör"
    assert "underleverantör" in terms(SLASH)


def test_en_dash_splits_words() -> None:
    # The pilot writes en dashes 75 times, e.g. "IT-konsulttjänster – IT-säkerhet".
    assert words("Konsulttjänster–Säkerhet") == ["konsulttjänster", "säkerhet"]


def test_ska_and_skall_are_stop_words() -> None:
    assert "ska" in words(IT_DRIFT)
    assert "ska" not in terms(IT_DRIFT)
    assert "skall" in words(SKALL)
    assert "skall" not in terms(SKALL)


def test_postgresql_stop_words_are_dropped() -> None:
    # "i", "är", "vem", "då" and "på" are in the list, "hos" is not.
    assert terms(SKALL) == [
        "fråg",
        "1",
        "scenariot",
        "konsult",
        "anställd",
        "hos",
        "underleverantör",
        "hos",
        "underleverantör",
        "skriv",
    ]


def test_stop_list_is_postgresql_17s_plus_ska_and_skall() -> None:
    postgresql = swedish_text._POSTGRESQL_STOP_WORDS

    assert len(postgresql) == 114
    assert len(set(postgresql)) == 114
    assert postgresql[:5] == ["och", "det", "att", "i", "en"]
    assert postgresql[-3:] == ["ert", "era", "vilkas"]
    assert STOP_WORDS - frozenset(postgresql) == {"ska", "skall"}
    assert frozenset(postgresql) <= STOP_WORDS


def test_definite_forms_have_the_stem_of_the_bare_form() -> None:
    # Snowball 3.0 removes -et; PostgreSQL 17's stemmer leaves "avtalet" as it is.
    assert terms("avtalet avtal avtalen avtalets") == ["avtal"] * 4
    assert terms("ramavtalet ramavtal") == ["ramavtal"] * 2
    assert terms(VALIDITY)[0] == terms(IT_DRIFT)[0] == "ramavtal"


def test_terms_keep_order_and_repeats() -> None:
    assert terms(PARTIES) == [
        "ramavtal",
        "avtalsnumm",
        "23.3.2649-22-003",
        "träffat",
        "avropsberätt",
        "räkning",
        "stat",
        "inköpscentral",
        "kammarkollegiet",
        "organisationsnumm",
        "202100-0829",
        "nedan",
        "kammarkollegiet",
        "crayon",
        "ab",
        "organisationsnumm",
        "556635-9799",
        "nedan",
        "ramavtalsleverantör",
    ]


def test_words_with_digits_are_not_stemmed() -> None:
    assert terms("iso27001 200 anställda") == ["iso27001", "200", "anställd"]


def test_swedish_letters_are_kept() -> None:
    assert terms("får far") == ["får", "far"]


def test_decomposed_letters_are_the_same_letter() -> None:
    decomposed = unicodedata.normalize("NFD", "uppsägningstid")

    assert decomposed != "uppsägningstid"
    assert terms(decomposed) == terms("uppsägningstid") == ["uppsägningstid"]


def test_query_terms_are_unique_in_first_seen_order() -> None:
    question = "Vilken uppsägningstid gäller för avtalet, och kan avtalet sägas upp i förtid?"

    assert query_terms(question) == ["uppsägningstid", "gäll", "avtal", "säg", "förtid"]


def test_a_question_of_stop_words_has_no_terms() -> None:
    assert query_terms("Vad ska vi ha?") == []
    assert query_terms("") == []


def test_analyser_names_the_stemmer_version() -> None:
    assert version("snowballstemmer") in ANALYSER
    assert ANALYSER.startswith("sv-1 ")
