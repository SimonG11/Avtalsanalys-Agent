"""Tests for avtalsagent.ingestion.llm_title_matcher.

No test calls OpenAI: the model is a fake that answers with a fixed number and
keeps the messages it got, and `ChatOpenAI` is replaced where the adapter is
built. The question is from the M4 pilot, 14aa1cc8ee3d §9.18.2 (avropa.se,
2026-10-05): "(enligt avsnitt Försäljningsredovisning och administrativ avgift)",
where the heading says "administrationsavgift".
"""

import json
from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models import LanguageModelInput
from langchain_core.runnables import RunnableLambda
from pydantic import SecretStr

from avtalsagent.config import Settings
from avtalsagent.ingestion import llm_title_matcher
from avtalsagent.ingestion.llm_title_matcher import (
    CACHE_FILE,
    HeadingChoice,
    OpenAITitleMatcher,
    openai_title_matcher,
)

PHRASE = "Försäljningsredovisning och administrativ avgift) de fyra (4) senaste kvartalen"
SENTENCE = (
    "Vite utgår med 0,25 % av Ramavtalsleverantörens rapporterade omsättning på Ramavtalet "
    "(enligt avsnitt Försäljningsredovisning och administrativ avgift) de fyra (4) senaste "
    "kvartalen för varje påbörjad vecka."
)
HEADINGS = (
    "9.15 Försäljningsredovisning och administrationsavgift",
    "9.9.1 Försäkringar",
)


class FakeModel:
    """Answers every question with `number`; `questions` holds what it was sent."""

    def __init__(self, number: int | None) -> None:
        self.number = number
        self.questions: list[Any] = []  # the messages, as the adapter sends them

    def answer(self, messages: LanguageModelInput) -> HeadingChoice:
        self.questions.append(messages)
        return HeadingChoice(number=self.number)

    def runnable(self) -> RunnableLambda[LanguageModelInput, HeadingChoice]:
        return RunnableLambda(self.answer)


def matcher(model: FakeModel, cache: Path, name: str = "gpt-6-luna") -> OpenAITitleMatcher:
    return OpenAITitleMatcher(model.runnable(), name, cache)


def test_the_answer_is_the_heading_with_the_number_the_model_gave(tmp_path: Path) -> None:
    model = FakeModel(1)
    title_matcher = matcher(model, tmp_path / CACHE_FILE)

    assert title_matcher(PHRASE, SENTENCE, HEADINGS) == HEADINGS[0]
    assert (title_matcher.calls, title_matcher.cache_hits) == (1, 0)


def test_the_model_gets_the_phrase_the_sentence_and_numbered_headings(tmp_path: Path) -> None:
    model = FakeModel(1)

    matcher(model, tmp_path / CACHE_FILE)(PHRASE, SENTENCE, HEADINGS)

    [[(system_role, instructions), (human_role, question)]] = model.questions
    assert (system_role, human_role) == ("system", "human")
    assert "null" in instructions
    assert f"Reference: {PHRASE}" in question
    assert f"Sentence: {SENTENCE}" in question
    assert "1. 9.15 Försäljningsredovisning och administrationsavgift\n2. 9.9.1" in question


@pytest.mark.parametrize("number", [None, 0, 3])
def test_null_or_a_number_outside_the_list_is_no_answer(tmp_path: Path, number: int | None) -> None:
    assert matcher(FakeModel(number), tmp_path / CACHE_FILE)(PHRASE, SENTENCE, HEADINGS) is None


def test_the_same_question_is_answered_from_the_cache(tmp_path: Path) -> None:
    model = FakeModel(1)
    title_matcher = matcher(model, tmp_path / CACHE_FILE)

    title_matcher(PHRASE, SENTENCE, HEADINGS)
    assert title_matcher(PHRASE, SENTENCE, HEADINGS) == HEADINGS[0]

    assert len(model.questions) == 1
    assert (title_matcher.calls, title_matcher.cache_hits) == (1, 1)


def test_a_second_run_reads_the_answers_of_the_first_and_makes_no_calls(tmp_path: Path) -> None:
    cache = tmp_path / "llm_cache" / CACHE_FILE
    matcher(FakeModel(1), cache)(PHRASE, SENTENCE, HEADINGS)
    matcher(FakeModel(None), cache)(PHRASE, SENTENCE, HEADINGS[::-1])  # cached as no answer

    model = FakeModel(2)
    second_run = matcher(model, cache)

    assert second_run(PHRASE, SENTENCE, HEADINGS) == HEADINGS[0]
    assert second_run(PHRASE, SENTENCE, HEADINGS[::-1]) is None
    assert model.questions == []
    assert (second_run.calls, second_run.cache_hits) == (0, 2)


def test_each_cache_line_shows_the_question_and_the_answer(tmp_path: Path) -> None:
    cache = tmp_path / CACHE_FILE
    matcher(FakeModel(1), cache)(PHRASE, SENTENCE, HEADINGS)

    [line] = cache.read_text(encoding="utf-8").splitlines()
    entry = json.loads(line)
    assert entry["model"] == "gpt-6-luna"
    assert entry["prompt_version"] == llm_title_matcher.PROMPT_VERSION
    assert (entry["phrase"], entry["context"]) == (PHRASE, SENTENCE)
    assert entry["candidates"] == list(HEADINGS)
    assert entry["answer"] == HEADINGS[0]


def test_another_sentence_model_or_prompt_version_is_a_new_question(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / CACHE_FILE
    model = FakeModel(1)
    matcher(model, cache)(PHRASE, SENTENCE, HEADINGS)

    matcher(model, cache)(PHRASE, "Vitet utgår enligt avsnitt Försäljningsredovisning.", HEADINGS)
    matcher(model, cache, name="gpt-6-astra")(PHRASE, SENTENCE, HEADINGS)
    monkeypatch.setattr(llm_title_matcher, "PROMPT_VERSION", "next")
    matcher(model, cache)(PHRASE, SENTENCE, HEADINGS)

    assert len(model.questions) == 4


def test_without_an_openai_key_there_is_no_matcher(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, openai_api_key=None, data_dir=tmp_path)

    assert openai_title_matcher(settings) is None


def test_the_matcher_uses_the_extraction_model_and_caches_under_the_data_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "sk-test-not-a-real-key"
    model = FakeModel(1)
    built: list[dict[str, Any]] = []

    class FakeChatOpenAI:
        def __init__(self, **kwargs: Any) -> None:
            built.append(kwargs)

        def with_structured_output(
            self, schema: type[HeadingChoice]
        ) -> RunnableLambda[LanguageModelInput, HeadingChoice]:
            assert schema is HeadingChoice
            return model.runnable()

    monkeypatch.setattr(llm_title_matcher, "ChatOpenAI", FakeChatOpenAI)
    settings = Settings(
        _env_file=None,
        openai_api_key=SecretStr(secret),
        extraction_model="gpt-6-luna",
        data_dir=tmp_path,
    )

    title_matcher = openai_title_matcher(settings)
    assert title_matcher is not None
    assert title_matcher(PHRASE, SENTENCE, HEADINGS) == HEADINGS[0]

    [kwargs] = built
    assert kwargs["model"] == "gpt-6-luna"
    assert kwargs["api_key"] == SecretStr(secret)  # passed on as a secret, never as text
    cache = tmp_path / "llm_cache" / CACHE_FILE
    assert cache.exists()
    assert secret not in cache.read_text(encoding="utf-8")
