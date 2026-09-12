"""Cloud-free embedding abstraction and deterministic local reference provider."""

from __future__ import annotations

import hashlib
import math
import re
from abc import ABC, abstractmethod
from collections.abc import Sequence

EMBEDDING_TASK_TYPES = frozenset({"RETRIEVAL_DOCUMENT", "RETRIEVAL_QUERY", "SEMANTIC_SIMILARITY"})


class EmbeddingProvider(ABC):
    """Explicit provider contract shared by local and production-target adapters."""

    @property
    @abstractmethod
    def provider_name(self) -> str: ...

    @property
    @abstractmethod
    def model_id(self) -> str: ...

    @property
    @abstractmethod
    def dimension(self) -> int: ...

    @property
    @abstractmethod
    def embedding_version(self) -> str: ...

    @abstractmethod
    def embed(self, texts: Sequence[str], *, task_type: str) -> list[list[float]]: ...


class DeterministicLocalEmbeddingProvider(EmbeddingProvider):
    """Stable token-hash vectors for tests, not a semantic production model."""

    def __init__(self, dimension: int = 64) -> None:
        if dimension < 8:
            raise ValueError("local embedding dimension must be at least 8")
        self._dimension = dimension

    @property
    def provider_name(self) -> str:
        return "deterministic-local"

    @property
    def model_id(self) -> str:
        return "token-hash-embedding-v1"

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def embedding_version(self) -> str:
        return f"{self.model_id}:dimension={self.dimension}"

    @staticmethod
    def _tokens(text: str) -> list[str]:
        return re.findall(r"[a-z0-9]+", text.casefold())

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        for token in self._tokens(text):
            digest = hashlib.sha256(token.encode()).digest()
            bucket = int.from_bytes(digest[:8], "big") % self.dimension
            sign = 1.0 if digest[8] & 1 else -1.0
            vector[bucket] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        if norm:
            vector = [value / norm for value in vector]
        return vector

    def embed(self, texts: Sequence[str], *, task_type: str) -> list[list[float]]:
        if task_type not in EMBEDDING_TASK_TYPES:
            raise ValueError(f"unsupported embedding task type: {task_type}")
        return [self._embed_one(text) for text in texts]
