"""Embeddings of chunks and questions: text to a vector of the search's vector branch.

What:
    `Embedder` is what step 6 and the search need: a name for the model and
    its settings, and vectors for texts. `OpenAIEmbedder` is one through the
    OpenAI API, and `openai_embedder(settings)` builds it from the settings,
    or gives None when no OpenAI key is set. `normalise` scales a vector to
    length 1.

Why:
    OpenAI's `text-embedding-3-large` embeds the pilot's chunks in minutes;
    a local model takes hours on a laptop's CPU (ADR 0011). The `openai`
    client is used directly rather than through LangChain, whose OpenAI
    embeddings count tokens with tiktoken, which must download its
    vocabulary. The name ("text-embedding-3-large:1536") is stored with the
    index and in the embedding cache, so vectors of different models or
    sizes are never compared. Vectors have length 1, so the inner product
    the search ranks by (`<#>`) is the cosine similarity.

How:
    Texts go to the API in batches of `batch_size`, with the configured
    `dimensions` (the API shortens the vector). Each answer's vectors are put
    back in the order of the texts by their `index`, and normalised here too,
    in case a model or a shortening leaves them a little off. Questions are
    embedded with the same call as chunks; the model takes no instruction.
    An empty or blank text is a ValueError before any call, since the API
    refuses it. An error from OpenAI (a revoked key, no network, a rate
    limit after the client's retries) becomes `EmbeddingError`, which names
    the error's type only: the error's own message can quote part of the key.
    The client keeps the SDK's timeout and retries (600 s, two retries) unless
    `openai_embedder` is given others: avtal-mcp embeds a question with a
    short timeout and one retry, so a hung API fails fast.
"""

import math
from collections.abc import Sequence
from typing import Any, Protocol

import openai

from avtalsagent.config import Settings

MAX_BATCH_SIZE = 2048  # texts per request the embeddings API accepts


class Embedder(Protocol):
    """Turns texts into vectors of length 1 for the search."""

    @property
    def name(self) -> str:
        """The model and its settings, e.g. "text-embedding-3-large:1536"."""
        ...

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """One vector per text, in the order of `texts`."""
        ...

    def embed_query(self, text: str) -> list[float]:
        """The vector of a question."""
        ...


class EmbeddingError(RuntimeError):
    """Raised when the embedding model cannot be reached or answers wrongly."""


class OpenAIEmbedder:
    """An `Embedder` through the OpenAI embeddings API."""

    def __init__(self, client: openai.OpenAI, model: str, dimensions: int, batch_size: int) -> None:
        if dimensions < 1:
            raise ValueError(f"an embedding needs at least one dimension, not {dimensions}")
        if not 1 <= batch_size <= MAX_BATCH_SIZE:
            raise ValueError(f"the batch size must be 1 to {MAX_BATCH_SIZE}, not {batch_size}")
        self._client = client
        self._model = model
        self._dimensions = dimensions
        self._batch_size = batch_size
        self.requests = 0  # requests sent to the API
        self.tokens = 0  # tokens the API counted (what it bills)

    @property
    def name(self) -> str:
        return f"{self._model}:{self._dimensions}"

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        for number, text in enumerate(texts):
            if not text.strip():
                raise ValueError(f"text {number} is empty; the embeddings API refuses it")
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            vectors.extend(self._embed_batch(texts[start : start + self._batch_size]))
        return vectors

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]

    def _embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        try:
            response = self._client.embeddings.create(
                input=list(texts),
                model=self._model,
                dimensions=self._dimensions,
                encoding_format="float",
            )
        except openai.OpenAIError as error:
            raise EmbeddingError(
                f"the embedding model {self.name} could not be reached ({type(error).__name__}). "
                "Fix the key or the connection and run again."
            ) from None
        self.requests += 1
        self.tokens += response.usage.total_tokens
        by_index = {item.index: item.embedding for item in response.data}
        if len(response.data) != len(texts) or by_index.keys() != set(range(len(texts))):
            raise EmbeddingError(
                f"the embedding model {self.name} answered {len(response.data)} vectors "
                f"for {len(texts)} texts"
            )
        vectors = [by_index[number] for number in range(len(texts))]
        if any(len(vector) != self._dimensions for vector in vectors):
            raise EmbeddingError(
                f"the embedding model {self.name} answered vectors of another dimension"
            )
        return [normalise(vector) for vector in vectors]


def openai_embedder(
    settings: Settings, *, timeout: float | None = None, max_retries: int | None = None
) -> OpenAIEmbedder | None:
    """The embedder of the configured model; None without an OpenAI key.

    `timeout` (seconds per request) and `max_retries` replace the client's
    defaults when given.
    """
    if settings.openai_api_key is None:
        return None
    options: dict[str, Any] = {}
    if timeout is not None:
        options["timeout"] = timeout
    if max_retries is not None:
        options["max_retries"] = max_retries
    client = openai.OpenAI(api_key=settings.openai_api_key.get_secret_value(), **options)
    return OpenAIEmbedder(
        client,
        settings.embedding_model,
        settings.embedding_dimensions,
        settings.embedding_batch_size,
    )


def normalise(vector: Sequence[float]) -> list[float]:
    """`vector` scaled to length 1 (L2); a ValueError for a zero or non-finite vector."""
    length = math.hypot(*vector)
    if length == 0 or not math.isfinite(length):
        raise ValueError(f"cannot normalise a vector of length {length}")
    return [value / length for value in vector]
