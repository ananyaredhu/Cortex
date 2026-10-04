"""Embedding behind a small interface.

Restricted text must never leave the trust boundary (AGENTS.md rule 5, ADR-002), so ingestion only
embeds locally. A hosted backend is refused here even if the environment asks for it.
"""
import hashlib
import math
import os
from typing import Protocol

DIM = 1024   # chunks.embedding is vector(1024) in db/init.sql


class Embedder(Protocol):
    model: str     # stored as chunks.embedding_model
    version: str   # stored as chunks.embedding_version; change it to force a re-embed

    def embed(self, texts: list[str]) -> list[list[float] | None]:
        """One vector per text, in order. None means "no vector" (the column is nullable)."""


class FakeEmbedder:
    """Deterministic vectors from a hash of the text. For tests only: the vectors mean nothing."""
    model = "fake"
    version = "1"

    def __init__(self, dim: int = DIM) -> None:
        self.dim = dim
        self.calls = 0          # texts embedded so far; tests use it to prove nothing was re-embedded

    def embed(self, texts: list[str]) -> list[list[float] | None]:
        self.calls += len(texts)
        return [self._vector(t) for t in texts]

    def _vector(self, text: str) -> list[float]:
        raw: list[float] = []
        counter = 0
        while len(raw) < self.dim:
            digest = hashlib.sha256(f"{counter}:{text}".encode()).digest()
            raw += [b / 255.0 - 0.5 for b in digest]
            counter += 1
        raw = raw[:self.dim]
        norm = math.sqrt(sum(x * x for x in raw)) or 1.0
        return [x / norm for x in raw]


class NullEmbedder:
    """Writes chunks without vectors, so text and ACL tokens are indexed before a model is installed."""
    model = "none"
    version = "0"

    def embed(self, texts: list[str]) -> list[list[float] | None]:
        return [None for _ in texts]


class BgeM3Embedder:
    """Local bge-m3 (1024 dimensions). The model loads on first use; needs requirements-embed.txt."""
    model = "bge-m3"
    version = "1"

    def __init__(self, model_name: str = "BAAI/bge-m3") -> None:
        self._model_name = model_name
        self._encoder = None

    def embed(self, texts: list[str]) -> list[list[float] | None]:
        if not texts:
            return []
        if self._encoder is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise RuntimeError("bge-m3 needs `pip install -r requirements-embed.txt`, "
                                   "or set EMBEDDING_BACKEND=none to index without vectors") from exc
            self._encoder = SentenceTransformer(self._model_name)
        vectors = self._encoder.encode(texts, normalize_embeddings=True)
        return [[float(x) for x in v] for v in vectors]


def from_env(backend: str | None = None) -> Embedder:
    """The embedder `EMBEDDING_BACKEND` names: bge-m3 (default, local) or none. Hosted backends are refused."""
    backend = (backend or os.environ.get("EMBEDDING_BACKEND") or "bge-m3").strip().lower()
    if backend == "bge-m3":
        return BgeM3Embedder()
    if backend == "none":
        return NullEmbedder()
    raise ValueError(f"EMBEDDING_BACKEND={backend!r} is not allowed for ingestion: document text may only be "
                     "embedded locally (use bge-m3 or none)")
