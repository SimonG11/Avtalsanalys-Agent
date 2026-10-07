"""Tests for avtalsagent.validation.review: what code makes of the reviewer's verdict.

The verdicts are written by hand; no model is called. The texts are
checked whole where the agent or the user reads them as they are.
"""

import pytest

from avtalsagent.validation.review import (
    NOTE_ITEM_LENGTH,
    REVIEW_FAILED_NOTE,
    ClaimReview,
    ReviewVerdict,
    Support,
    review_passed,
    review_problems,
    review_reservations,
)

THREE_MONTHS = "Leverantören får säga upp avtalet med tre månaders varsel"


def claim(
    text: str, support: Support, ids: list[int] | None = None, reason: str = ""
) -> ClaimReview:
    return ClaimReview(
        claim=text, citation_ids=[1] if ids is None else ids, support=support, reason=reason
    )


def verdict(*claims: ClaimReview, missing: list[str] | None = None) -> ReviewVerdict:
    return ReviewVerdict(claims=list(claims), missing=missing or [])


def test_an_answer_whose_every_claim_is_supported_passes() -> None:
    supported = verdict(claim(THREE_MONTHS, "supported"), claim("Avtalet gäller", "supported"))

    assert review_passed(supported)
    assert review_problems(supported) == []
    assert review_reservations(supported) == []


def test_a_claim_supported_only_in_part_fails_the_answer() -> None:
    assert not review_passed(verdict(claim(THREE_MONTHS, "supported"), claim("x", "partly")))


def test_an_unsupported_claim_fails_the_answer() -> None:
    assert not review_passed(verdict(claim(THREE_MONTHS, "unsupported")))


def test_something_missing_fails_the_answer_though_every_claim_is_supported() -> None:
    assert not review_passed(verdict(claim(THREE_MONTHS, "supported"), missing=["IT-drift"]))


def test_a_missing_item_without_text_is_not_something_missing() -> None:
    blank = verdict(claim(THREE_MONTHS, "supported"), missing=["", "  \n"])

    assert review_passed(blank)
    assert review_problems(blank) == []


def test_a_verdict_without_claims_passes() -> None:
    assert review_passed(verdict())


def test_the_feedback_quotes_an_unsupported_claim_with_its_source_and_the_reason() -> None:
    wrong = verdict(claim(THREE_MONTHS, "unsupported", [1], "Avsnittet anger sex månader."))

    assert review_problems(wrong) == [
        "”Leverantören får säga upp avtalet med tre månaders varsel” [1]: stöds inte. "
        "Avsnittet anger sex månader."
    ]


def test_the_feedback_says_partly_and_names_each_source_once() -> None:
    partly = verdict(claim("Vitet är 5 %", "partly", [2, 1, 2], "Bara vid försening"))

    assert review_problems(partly) == [
        "”Vitet är 5 %” [2][1]: stöds bara delvis. Bara vid försening."
    ]


def test_a_claim_without_sources_or_reason_is_named_without_them() -> None:
    assert review_problems(verdict(claim("Avtalet gäller", "unsupported", []))) == [
        "”Avtalet gäller”: stöds inte."
    ]


def test_the_feedback_lists_the_claims_in_the_verdicts_order_then_what_is_missing() -> None:
    mixed = verdict(
        claim("Första", "unsupported", [1], "Fel."),
        claim("Andra", "supported"),
        claim("Tredje", "partly", [2], "Delvis."),
        missing=["frågan gällde också IT-drift Mindre", "Priset."],
    )

    assert review_problems(mixed) == [
        "”Första” [1]: stöds inte. Fel.",
        "”Tredje” [2]: stöds bara delvis. Delvis.",
        "Saknas: frågan gällde också IT-drift Mindre.",
        "Saknas: Priset.",
    ]


def test_the_models_texts_are_put_on_one_line_and_its_own_quote_marks_dropped() -> None:
    messy = verdict(claim("  ”Tre\nmånader”  ", "unsupported", [1], "Sex\n  månader"))

    assert review_problems(messy) == ["”Tre månader” [1]: stöds inte. Sex månader."]


def test_a_claims_own_full_stop_is_left_out_of_the_quote() -> None:
    # The live reviewer ends its claims with a full stop; the notes add their own.
    stopped = verdict(claim("Uppsägningstiden är tre månader.", "unsupported", [1]))

    assert review_problems(stopped) == ["”Uppsägningstiden är tre månader” [1]: stöds inte."]
    assert review_reservations(stopped) == [
        "Granskningen fann inte fullt stöd i källorna för: ”Uppsägningstiden är tre månader”."
    ]


def test_a_claim_without_text_is_still_named_as_a_claim() -> None:
    assert review_problems(verdict(claim(" ", "unsupported", [3], "Fel."))) == [
        "Ett påstående [3]: stöds inte. Fel."
    ]


def test_a_long_claim_is_cut_at_a_word_in_the_feedback() -> None:
    long_claim = "ord " * 200

    (line,) = review_problems(verdict(claim(long_claim, "unsupported", [1])))

    quoted = line.split("” [1]")[0]
    assert quoted.endswith("ord…")
    assert len(quoted) < 310


def test_the_user_is_told_which_claims_lack_full_support() -> None:
    doubted = verdict(
        claim(THREE_MONTHS, "unsupported", [1], "Sex månader."),
        claim("Avtalet gäller", "supported"),
        claim("Vitet är 5 %", "partly", [2]),
    )

    assert review_reservations(doubted) == [
        "Granskningen fann inte fullt stöd i källorna för: ”Leverantören får säga upp avtalet "
        "med tre månaders varsel” och ”Vitet är 5 %”."
    ]


def test_the_user_is_told_the_answer_may_be_incomplete() -> None:
    incomplete = verdict(
        claim(THREE_MONTHS, "supported"), missing=["IT-drift Mindre.", "priset per timme"]
    )

    assert review_reservations(incomplete) == [
        "Svaret kan vara ofullständigt: IT-drift Mindre och priset per timme."
    ]


def test_the_notes_name_at_most_three_claims_and_count_the_rest() -> None:
    many = verdict(*(claim(f"Påstående {n}", "unsupported") for n in range(1, 6)))
    one_more = verdict(*(claim(f"Påstående {n}", "partly") for n in range(1, 5)))

    assert review_reservations(many) == [
        "Granskningen fann inte fullt stöd i källorna för: ”Påstående 1”, ”Påstående 2”, "
        "”Påstående 3” och 2 påståenden till."
    ]
    assert review_reservations(one_more)[0].endswith("”Påstående 3” och ett påstående till.")


def test_the_notes_list_missing_items_apart_and_count_the_rest() -> None:
    missing = verdict(missing=["a, b", "c", "d", "e"])

    assert review_reservations(missing) == [
        "Svaret kan vara ofullständigt: a, b; c; d och en punkt till."
    ]


def test_the_notes_cut_a_long_claim_and_are_never_more_than_two() -> None:
    worst = verdict(
        claim("långt " * 100, "unsupported"),
        claim("kort", "partly"),
        missing=["x"],
    )

    notes = review_reservations(worst)

    assert len(notes) == 2
    first_claim = notes[0].split(": ", 1)[1].split("”")[1]
    assert first_claim.endswith("…")
    assert len(first_claim) <= NOTE_ITEM_LENGTH


def test_the_note_for_a_failed_review_is_fixed() -> None:
    assert REVIEW_FAILED_NOTE == "Svaret kunde inte granskas."


@pytest.mark.parametrize("ending", ["?", "!", "…"])
def test_a_missing_item_that_ends_a_sentence_gets_no_second_full_stop(ending: str) -> None:
    verdict = ReviewVerdict(claims=[], missing=[f"Gäller detta även IT-drift Mindre{ending}"])

    assert review_reservations(verdict) == [
        f"Svaret kan vara ofullständigt: Gäller detta även IT-drift Mindre{ending}"
    ]
