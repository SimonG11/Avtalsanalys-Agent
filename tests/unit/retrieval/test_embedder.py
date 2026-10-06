"""Tests for avtalsagent.retrieval.embedder.

No test calls OpenAI: the client is a fake that answers with fixed vectors and
keeps the requests it got.
"""

import math
from collections.abc import Sequence
from typing import Any, cast

import openai
import pytest
from openai.types import CreateEmbeddingResponse, Embedding
from openai.types.create_embedding_response import Usage
from pydantic import SecretStr

from avtalsagent.config import Settings
from avtalsagent.retrieval.embedder import (
    Embedder,
    EmbeddingError,
    OpenAIEmbedder,
    normalise,
    openai_embedder,
)

SECRET = "sk-test-not-a-real-key"


class FakeEmbeddings:
    """The `embeddings` resource: a vector [len(text), position, 1] per text."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.reverse = False  # answer the vectors out of order
        self.drop_last = False  # answer one vector too few
        self.error: Exception | None = None

    def create(self, **request: Any) -> CreateEmbeddingResponse:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        texts: list[str] = request["input"]
        data = [
            Embedding(embedding=[len(text), index, 1.0], index=index, object="embedding")
            for index, text in enumerate(texts)
        ]
        if self.reverse:
            data.reverse()
        if self.drop_last:
            data = data[:-1]
        return CreateEmbeddingResponse(
            data=data,
            model=request["model"],
            object="list",
            usage=Usage(prompt_tokens=len(texts), total_tokens=len(texts)),
        )


class FakeClient:
    def __init__(self) -> None:
        self.embeddings = FakeEmbeddings()


def embedder(batch_size: int = 2, dimensions: int = 3) -> tuple[OpenAIEmbedder, FakeEmbeddings]:
    client = FakeClient()
    built = OpenAIEmbedder(
        cast(openai.OpenAI, client), "text-embedding-3-large", dimensions, batch_size
    )
    return built, client.embeddings


def expected(text: str, position: int) -> list[float]:
    return normalise([float(len(text)), float(position), 1.0])


def test_the_name_has_the_model_and_the_dimensions() -> None:
    built, _ = embedder()

    assert built.name == "text-embedding-3-large:3"


def test_it_is_an_embedder() -> None:
    built, _ = embedder()
    as_protocol: Embedder = built

    assert as_protocol.name == built.name


def test_texts_are_sent_in_batches_with_the_model_and_dimensions() -> None:
    built, api = embedder(batch_size=2)
    texts = ["Uppsägning", "Vite", "Försening", "Ansvar", "Sekretess"]

    vectors = built.embed_documents(texts)

    assert [request["input"] for request in api.requests] == [
        ["Uppsägning", "Vite"],
        ["Försening", "Ansvar"],
        ["Sekretess"],
    ]
    assert {request["model"] for request in api.requests} == {"text-embedding-3-large"}
    assert {request["dimensions"] for request in api.requests} == {3}
    assert vectors == [expected(text, n % 2) for n, text in enumerate(texts)]
    assert (built.requests, built.tokens) == (3, 5)


def test_vectors_are_put_back_in_order_by_index() -> None:
    built, api = embedder(batch_size=10)
    api.reverse = True
    texts = ["a", "bb", "ccc"]

    assert built.embed_documents(texts) == [expected(text, n) for n, text in enumerate(texts)]


def test_vectors_are_normalised() -> None:
    built, _ = embedder()

    for vector in built.embed_documents(["Ramavtalsleverantörens uppsägningsrätt", "x"]):
        assert math.hypot(*vector) == pytest.approx(1.0)


def test_a_question_is_embedded_with_the_same_call() -> None:
    built, api = embedder()
    question = "Vilken uppsägningstid gäller?"

    assert built.embed_query(question) == built.embed_documents([question])[0]
    assert api.requests[0] == api.requests[1]
    assert api.requests[0]["input"] == [question]  # no instruction added


def test_no_texts_no_request() -> None:
    built, api = embedder()

    assert built.embed_documents([]) == []
    assert api.requests == []


@pytest.mark.parametrize("blank", ["", "   ", "\n\t"])
def test_an_empty_text_is_refused_before_any_request(blank: str) -> None:
    built, api = embedder()

    with pytest.raises(ValueError, match="text 1 is empty"):
        built.embed_documents(["Vite", blank])
    assert api.requests == []


def test_an_openai_error_names_its_type_but_not_its_message() -> None:
    built, api = embedder()
    api.error = openai.OpenAIError(f"Incorrect API key provided: {SECRET}")

    with pytest.raises(EmbeddingError) as raised:
        built.embed_documents(["Vite"])

    assert "OpenAIError" in str(raised.value)
    assert "text-embedding-3-large:3" in str(raised.value)
    assert SECRET not in str(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__


def test_a_missing_vector_is_an_error() -> None:
    built, api = embedder()
    api.drop_last = True

    with pytest.raises(EmbeddingError, match="1 vectors for 2 texts"):
        built.embed_documents(["Vite", "Ansvar"])


def test_a_vector_of_another_dimension_is_an_error() -> None:
    built, _ = embedder(dimensions=4)

    with pytest.raises(EmbeddingError, match="another dimension"):
        built.embed_documents(["Vite"])


@pytest.mark.parametrize(("dimensions", "batch_size"), [(0, 10), (3, 0), (3, 2049)])
def test_settings_out_of_range_are_refused(dimensions: int, batch_size: int) -> None:
    with pytest.raises(ValueError):
        embedder(batch_size=batch_size, dimensions=dimensions)


def test_normalise() -> None:
    assert normalise([3.0, 4.0]) == [0.6, 0.8]
    assert normalise([0.0, -2.0]) == [0.0, -1.0]


@pytest.mark.parametrize("vector", [[0.0, 0.0], [math.inf, 1.0], [math.nan, 1.0]])
def test_normalise_refuses_a_vector_without_a_direction(vector: Sequence[float]) -> None:
    with pytest.raises(ValueError, match="cannot normalise"):
        normalise(vector)


def test_without_an_openai_key_there_is_no_embedder() -> None:
    settings = Settings(_env_file=None, openai_api_key=None)

    assert openai_embedder(settings) is None


def test_the_embedder_follows_the_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    built_clients: list[dict[str, Any]] = []

    def fake_openai(**kwargs: Any) -> FakeClient:
        built_clients.append(kwargs)
        return FakeClient()

    monkeypatch.setattr(openai, "OpenAI", fake_openai)
    settings = Settings(
        _env_file=None,
        openai_api_key=SecretStr(SECRET),
        embedding_model="text-embedding-3-small",
        embedding_dimensions=3,
        embedding_batch_size=1,
    )

    built = openai_embedder(settings)

    assert built is not None
    assert built.name == "text-embedding-3-small:3"
    assert built_clients == [{"api_key": SECRET}]  # the key goes to the client only
    built.embed_documents(["Vite", "Ansvar"])
    assert built.requests == 2  # one text per request
