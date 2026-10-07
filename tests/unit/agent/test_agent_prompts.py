"""Tests for avtalsagent.agent.prompts and .ask_user: what the model is told.

The prompt's wording is reviewed by people; these tests keep what the code
relies on: the date line, the tool names the prompt uses, and the Swedish
schemas the model reads for `ask_user` and `FinalAnswer`.
"""

import inspect
import re
from datetime import UTC, date, datetime, tzinfo
from typing import get_type_hints
from zoneinfo import ZoneInfoNotFoundError

import pytest
from langchain_core.utils.function_calling import convert_to_openai_tool

import avtalsagent.agent.prompts as prompts
from avtalsagent.agent.ask_user import MAX_OPTIONS, MIN_OPTIONS, ask_user
from avtalsagent.agent.prompts import SYSTEM_PROMPT, system_prompt, today_in_sweden
from avtalsagent.agent.schemas import DraftCitation, FinalAnswer
from avtalsagent.mcp_server.tools import TOOLS


def test_the_prompt_ends_with_the_date() -> None:
    prompt = system_prompt(date(2026, 10, 7))

    assert prompt.endswith("\n\nDagens datum: 2026-10-07.")
    assert prompt.count("2026-10-07") == 1
    assert "{" not in prompt and "}" not in prompt


def test_the_prompt_names_every_tool_the_agent_has() -> None:
    names = [tool.__name__ for tool in TOOLS] + ["ask_user", "FinalAnswer"]

    assert [name for name in names if name not in SYSTEM_PROMPT] == []


def test_the_prompt_names_only_fields_the_tools_and_the_answer_have() -> None:
    named = set(re.findall(r"\b[a-z]+_[a-z_]+\b", SYSTEM_PROMPT))
    tools = {tool.__name__ for tool in TOOLS} | {"ask_user"}
    arguments = {name for tool in TOOLS for name in inspect.signature(tool).parameters}
    results = {name for tool in TOOLS for name in get_type_hints(tool)["return"].model_fields}
    answer = set(FinalAnswer.model_fields) | set(DraftCitation.model_fields)

    assert named - tools - arguments - results - answer == set()
    # The ones the live test showed the model needs told.
    assert {"framework_area", "agreement_number", "section_position", "page_title"} <= named


def test_today_in_sweden_is_the_date_in_sweden_not_utc(monkeypatch: pytest.MonkeyPatch) -> None:
    class LateEvening(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> "LateEvening":
            # 22:30 UTC is 00:30 the next day in Sweden (summer time).
            moment = datetime(2026, 10, 6, 22, 30, tzinfo=UTC).astimezone(tz)
            return cls.fromtimestamp(moment.timestamp(), tz)

    monkeypatch.setattr(prompts, "datetime", LateEvening)

    assert today_in_sweden() == date(2026, 10, 7)


def test_today_in_sweden_falls_back_to_the_machines_date_without_time_zones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_zones(key: str) -> None:
        raise ZoneInfoNotFoundError(key)

    monkeypatch.setattr(prompts, "ZoneInfo", no_zones)

    assert today_in_sweden() == date.today()


def test_ask_user_is_described_to_the_model_in_swedish() -> None:
    schema = convert_to_openai_tool(ask_user)["function"]["parameters"]

    assert ask_user.name == "ask_user"
    assert ask_user.description.startswith("Fråga användaren")
    assert list(schema["properties"]) == ["question", "options"]
    assert schema["required"] == ["question", "options"]
    assert schema["properties"]["question"]["description"].startswith("Frågan")
    options = schema["properties"]["options"]
    assert (options["minItems"], options["maxItems"]) == (MIN_OPTIONS, MAX_OPTIONS) == (2, 5)
    assert options["items"] == {"type": "string", "minLength": 1}
    assert options["description"].startswith("2-5 korta svarsalternativ")


def test_the_prompt_asks_for_an_answer_per_case_before_a_question_with_options() -> None:
    # Rule 4 (ADR 0025): answer for each of a few cases; ask only otherwise, with the options
    # the schema requires.
    [rule] = [line for line in SYSTEM_PROMPT.splitlines() if line.startswith("4. ")]

    assert "svara för vart och ett" in rule and "säg vad som avgör" in rule
    assert "Fråga med ask_user bara när" in rule
    assert f"Ge då {MIN_OPTIONS}-{MAX_OPTIONS} korta alternativ i options" in rule


def test_final_answer_is_described_to_the_model_in_swedish() -> None:
    schema = FinalAnswer.model_json_schema()
    citation = schema["$defs"]["DraftCitation"]

    assert schema["description"].startswith("Lämna ditt slutliga svar")
    assert schema["required"] == ["answered", "text"]
    assert list(citation["properties"]) == [
        "id",
        "sha256",
        "section_position",
        "quote",
        "page_title",
    ]
    assert citation["required"] == ["id", "sha256", "section_position", "quote"]
    assert all("description" in field for field in citation["properties"].values())
