"""Embedders. All embedding goes through the `Embedder` protocol (see CLAUDE.md)."""

import hashlib
import itertools
import math
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol

EMBEDDING_DIM = 384  # kb.chunks.embedding is vector(384)


class Embedder(Protocol):
    @property
    def name(self) -> str:
        """Identifies the model; stored with each chunk so search can refuse a mismatched index."""
        ...

    @property
    def dim(self) -> int: ...

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class FastEmbedder:
    """bge-small-en-v1.5 via fastembed (ONNX, no torch). The model downloads on first use."""

    def __init__(self, model: str = "BAAI/bge-small-en-v1.5", cache_dir: Path | None = None):
        self._model_name = model
        self._cache_dir = cache_dir
        self._model: Any = None

    def _load(self) -> Any:
        if self._model is None:
            from fastembed import TextEmbedding  # heavy import; keep it out of unit tests

            cache = str(self._cache_dir) if self._cache_dir else None
            self._model = TextEmbedding(self._model_name, cache_dir=cache)
        return self._model

    @property
    def name(self) -> str:
        return f"fastembed:{self._model_name}"

    @property
    def dim(self) -> int:
        return EMBEDDING_DIM

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [v.tolist() for v in self._load().passage_embed(list(texts))]

    def embed_query(self, text: str) -> list[float]:
        # bge expects an instruction prefix on queries; fastembed's query_embed adds it.
        return [float(x) for x in next(iter(self._load().query_embed(text)))]


_TOKEN = re.compile(r"[a-z0-9]+")


class HashEmbedder:
    """Deterministic feature-hashing embedder for tests: no model, no network.

    Tokens (lowercased alphanumerics) and their bigrams hash into signed buckets, then the vector
    is L2-normalised, so texts sharing words have positive cosine similarity.
    """

    def __init__(self, dim: int = EMBEDDING_DIM) -> None:
        self._dim = dim

    @property
    def name(self) -> str:
        return f"hash:{self._dim}"

    @property
    def dim(self) -> int:
        return self._dim

    def _vector(self, text: str) -> list[float]:
        tokens = _TOKEN.findall(text.lower())
        features = tokens + [f"{a} {b}" for a, b in itertools.pairwise(tokens)]
        vec = [0.0] * self._dim
        for feature in features:
            digest = hashlib.blake2b(feature.encode(), digest_size=8).digest()
            value = int.from_bytes(digest, "big")
            vec[value % self._dim] += 1.0 if (value >> 32) & 1 else -1.0
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


def make_embedder(kind: str, model: str, cache_dir: Path | None = None) -> Embedder:
    if kind == "hash":
        return HashEmbedder()
    if kind == "fastembed":
        return FastEmbedder(model, cache_dir)
    raise ValueError(f"unknown embedder {kind!r}")
