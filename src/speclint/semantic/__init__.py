"""Semantic-tier infrastructure — deterministic embeddings for spec linting.

Two embedder implementations, both honoring the `Embedder` protocol:

  - `FastembedEmbedder` — production. Wraps fastembed (ONNX runtime,
    BAAI/bge-small-en-v1.5 by default). Lazy import so the core install
    stays light; opt-in via `pip install spec-lint[semantic]`.

  - `BagOfTokensEmbedder` — test/fallback. Pure-Python token-frequency
    vectorizer, no deps. Not as good as a real embedding model, but
    deterministic and good enough for fixture-driven rule tests.

Rules consume embeddings via `ir.embedder`. The runner instantiates one
embedder per run if any semantic-tier rule is loaded; if fastembed isn't
available, semantic rules are dropped with a skip entry (mirrors the
LLM-tier graceful-skip pattern).
"""
from __future__ import annotations

import importlib.util
import math
import re
from typing import Protocol, runtime_checkable


@runtime_checkable
class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...

    @property
    def name(self) -> str: ...


def cosine_sim(a: list[float], b: list[float]) -> float:
    """Cosine similarity in [-1, 1]. Returns 0.0 if either vector is zero."""
    if len(a) != len(b):
        raise ValueError(f"vector length mismatch: {len(a)} vs {len(b)}")
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def fastembed_available() -> bool:
    """True if `fastembed` is importable. Doesn't actually import — uses
    `find_spec` so we don't pay the heavy ONNX init cost unnecessarily."""
    return importlib.util.find_spec("fastembed") is not None


class BagOfTokensEmbedder:
    """Deterministic token-frequency embedder. No deps, no ML.

    Tokenizes each input by lowercasing + splitting on non-alphanumeric.
    Returns vectors over the union of tokens seen in the batch. Cosine
    similarity then measures token overlap — enough signal for fixtures
    that intentionally pair semantically-overlapping vs disjoint inputs.
    """

    name = "bag-of-tokens"

    _TOKEN_RE = re.compile(r"[a-z0-9]+")

    def embed(self, texts: list[str]) -> list[list[float]]:
        token_lists = [self._TOKEN_RE.findall(t.lower()) for t in texts]
        vocab: dict[str, int] = {}
        for tokens in token_lists:
            for tok in tokens:
                if tok not in vocab:
                    vocab[tok] = len(vocab)

        vectors: list[list[float]] = []
        for tokens in token_lists:
            vec = [0.0] * len(vocab)
            for tok in tokens:
                vec[vocab[tok]] += 1.0
            vectors.append(vec)
        return vectors


class FastembedEmbedder:
    """Lazy wrapper around fastembed's TextEmbedding. Import + model load
    deferred to first `.embed()` call so importing this module doesn't
    drag in the ~200MB ONNX runtime."""

    def __init__(self, model: str = "BAAI/bge-small-en-v1.5"):
        if not fastembed_available():
            raise SemanticUnavailable(
                "fastembed is not installed. "
                "Install with: pip install 'spec-lint[semantic]'"
            )
        self._model_name = model
        self._impl = None  # populated on first embed()

    @property
    def name(self) -> str:
        return f"fastembed:{self._model_name}"

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self._impl is None:
            from fastembed import TextEmbedding  # type: ignore

            self._impl = TextEmbedding(model_name=self._model_name)
        return [list(v) for v in self._impl.embed(texts)]


class SemanticUnavailable(Exception):
    """Raised when a semantic operation is attempted without fastembed
    installed. Caught by the runner to drive graceful skip."""


__all__ = [
    "BagOfTokensEmbedder",
    "Embedder",
    "FastembedEmbedder",
    "SemanticUnavailable",
    "cosine_sim",
    "fastembed_available",
]
