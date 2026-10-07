"""The language model that chooses a heading for `extract/title_matcher.py`.

What:
    `openai_title_matcher(settings)` gives an `OpenAITitleMatcher`, a
    `TitleMatcher` that asks the extraction model (`EXTRACTION_MODEL`, ADR
    0005) which of the candidate headings a reference means. It gives None
    when no OpenAI key is set, and step 4 then runs without the model. Every
    answer is kept in `data/llm_cache/title_matcher.jsonl`, so a second run over
    the same files makes no calls.

Why:
    Calls to a model are kept in an adapter, apart from the rules (architecture
    plan, section 4), so the rules stay pure and are tested without a network. The
    cache makes a run repeatable and its cost visible: the ingestion report
    gives the calls made and the answers taken from the cache. Each line of the
    cache holds the question and the answer as text, so a reviewer can read
    what the model chose without running anything.

How:
    `ChatOpenAI(...).with_structured_output(HeadingChoice)`: the model answers
    with the number of a candidate, or null, which is easier to check than a
    copied heading. A number outside the list counts as no answer. An answer is
    cached under a hash of the model name, `PROMPT_VERSION`, the phrase, the
    sentence and the candidates; a new prompt gets a new `PROMPT_VERSION`, so
    old answers are not reused. A cache line that cannot be read (cut short by
    a full disk or a killed process) is skipped with a warning, and the next
    answer starts on a new line. An error from OpenAI (a revoked key, no
    network) stops the run with `LanguageModelError`, which names the error's
    type: taking it as no answer would make the rate depend on the network.
    The key comes from pydantic-settings as a `SecretStr` and goes only to
    `ChatOpenAI`; it is never printed or logged, and the OpenAI error's own
    message, which can quote part of it, is not passed on.
"""

import hashlib
import json
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from langchain_core.language_models import LanguageModelInput
from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI
from openai import OpenAIError
from pydantic import BaseModel, Field

from avtalsagent.config import Settings

_log = logging.getLogger(__name__)

PROMPT_VERSION = "2"
CACHE_FILE = "title_matcher.jsonl"  # under settings.data_dir / "llm_cache"

# Version 1 said only "choose the heading the reference means"; the model then often
# chose a heading on a related subject ("Rätt att avsluta Kontrakt" -> "Rättelse vid
# avtalsbrott"), so version 2 says what counts as the same section.
_INSTRUCTIONS = """\
You resolve cross-references in Swedish procurement and framework-agreement documents.
You get a reference, the sentence it is in, and a numbered list of headings from the one
file the reference most likely points to. No heading has exactly the reference's title.

The reference is one of two kinds.
1. A section title ("avsnitt X", "kapitel X", "punkt X"). The reference text may run on
   past the title into the rest of the sentence; ignore that part. Choose a heading only
   if it is the same section with a slightly different wording: another inflection or
   spelling, a typo, a word added or left out, or only the start of the title.
   Same section: "Försäljningsredovisning och administrativ avgift" and "9.15
   Försäljningsredovisning och administrationsavgift"; "Skäl för" and "3.2 Skäl för
   uteslutning". Not the same section, only a related subject: "Rätt att avsluta
   Kontrakt" and "5.16.1 Rättelse vid avtalsbrott".
2. A topic in a document ("villkor i Allmänna villkor gällande viten och skadestånd").
   Choose the heading of the section that covers the whole topic. When the topic names
   several subjects, that is the chapter that holds them all, if there is one.

Answer null when no heading is clearly meant; when the sentence places the section in
another document, form or annex than the one the headings seem to come from ("i
Ramavtalets Huvuddokument", "i bilaga Konsultkompetens", "i upphandlingsdokumenten");
or when the reference is not a title at all. A wrong section is worse than none."""


class LanguageModelError(RuntimeError):
    """The model could not be asked; the run stops rather than go on without its answers."""


class HeadingChoice(BaseModel):
    """The model's answer."""

    number: int | None = Field(
        description="The number of the heading the reference means, or null if none of them."
    )


class OpenAITitleMatcher:
    """A `TitleMatcher` that asks a model and remembers its answers in a file."""

    def __init__(
        self, model: Runnable[LanguageModelInput, Any], model_name: str, cache_file: Path
    ) -> None:
        self._model = model
        self._model_name = model_name
        self._cache_file = cache_file
        text = cache_file.read_text(encoding="utf-8") if cache_file.exists() else ""
        self._answers = _read_cache(text, cache_file)
        # After a line cut short, the next answer must start on a line of its own.
        self._new_line_first = bool(text) and not text.endswith("\n")
        self.calls = 0  # requests sent to the model
        self.cache_hits = 0  # answers taken from the cache

    def __call__(self, phrase: str, context: str, candidates: Sequence[str]) -> str | None:
        key = _cache_key(self._model_name, phrase, context, candidates)
        if key in self._answers:
            self.cache_hits += 1
            return self._answers[key]
        listed = "\n".join(f"{number}. {heading}" for number, heading in enumerate(candidates, 1))
        question = f"Reference: {phrase}\nSentence: {context}\nHeadings:\n{listed}"
        try:
            choice = self._model.invoke([("system", _INSTRUCTIONS), ("human", question)])
        except OpenAIError as error:
            raise LanguageModelError(
                f"the language model {self._model_name} could not be asked "
                f"({type(error).__name__}); the answers so far are kept in "
                f"{self._cache_file}. Fix the key or the connection and run again, "
                "or run without OPENAI_API_KEY to leave the model out."
            ) from None
        self.calls += 1
        number = choice.number if isinstance(choice, HeadingChoice) else None
        answer = candidates[number - 1] if number and 1 <= number <= len(candidates) else None
        self._answers[key] = answer
        line = {
            "key": key,
            "model": self._model_name,
            "prompt_version": PROMPT_VERSION,
            "phrase": phrase,
            "context": context,
            "candidates": list(candidates),
            "answer": answer,
        }
        self._cache_file.parent.mkdir(parents=True, exist_ok=True)
        start = "\n" if self._new_line_first else ""
        with self._cache_file.open("a", encoding="utf-8") as cache:
            cache.write(start + json.dumps(line, ensure_ascii=False) + "\n")
        self._new_line_first = False
        return answer


def openai_title_matcher(settings: Settings) -> OpenAITitleMatcher | None:
    """The matcher for the configured extraction model; None without an OpenAI key."""
    if settings.openai_api_key is None:
        return None
    # The model only accepts its default temperature, so none is set; the cache
    # keeps the answers of a run fixed.
    model = ChatOpenAI(model=settings.extraction_model, api_key=settings.openai_api_key)
    return OpenAITitleMatcher(
        model.with_structured_output(HeadingChoice),
        settings.extraction_model,
        settings.data_dir / "llm_cache" / CACHE_FILE,
    )


def _cache_key(model_name: str, phrase: str, context: str, candidates: Sequence[str]) -> str:
    question = [model_name, PROMPT_VERSION, phrase, context, list(candidates)]
    return hashlib.sha256(json.dumps(question, ensure_ascii=False).encode()).hexdigest()


def _read_cache(text: str, path: Path) -> dict[str, str | None]:
    """The answers by key; a line that is not a whole entry is skipped with a warning."""
    answers: dict[str, str | None] = {}
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
            answers[entry["key"]] = entry["answer"]
        except (ValueError, TypeError, KeyError):
            _log.warning("%s line %d is not a cache entry; skipped", path, number)
    return answers
