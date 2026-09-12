"""Statically validated Vertex embedding contract with an explicitly injected client."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from enterprise_platform.embeddings import EMBEDDING_TASK_TYPES, EmbeddingProvider


class VertexPredictionClient(Protocol):
    """Small boundary implemented by a caller-owned SDK client or a local fake."""

    def predict(self, request: Mapping[str, Any]) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class VertexEmbeddingConfig:
    model_id: str = "gemini-embedding-001"
    output_dimension: int = 3072
    batch_size: int = 1
    auto_truncate: bool = False
    contract_version: str = "vertex-predict-v1"

    def __post_init__(self) -> None:
        if self.model_id != "gemini-embedding-001":
            raise ValueError("this validated contract supports only gemini-embedding-001")
        if not 1 <= self.output_dimension <= 3072:
            raise ValueError("output_dimension must be between 1 and 3072")
        if self.batch_size != 1:
            raise ValueError("gemini-embedding-001 accepts one input per request")


class VertexEmbeddingContract(EmbeddingProvider):
    """Build and validate requests without constructing credentials or a cloud client."""

    def __init__(
        self,
        *,
        client: VertexPredictionClient,
        config: VertexEmbeddingConfig | None = None,
    ) -> None:
        if client is None:
            raise ValueError("an explicitly injected Vertex prediction client is required")
        self.client = client
        self.config = config or VertexEmbeddingConfig()

    @property
    def provider_name(self) -> str:
        return "vertex-ai"

    @property
    def model_id(self) -> str:
        return self.config.model_id

    @property
    def dimension(self) -> int:
        return self.config.output_dimension

    @property
    def embedding_version(self) -> str:
        return f"{self.config.contract_version}:{self.model_id}:dimension={self.dimension}"

    def build_requests(self, texts: Sequence[str], *, task_type: str) -> list[dict[str, Any]]:
        if task_type not in EMBEDDING_TASK_TYPES:
            raise ValueError(f"unsupported embedding task type: {task_type}")
        return [
            {
                "model": self.model_id,
                "instances": [{"content": text, "task_type": task_type}],
                "parameters": {
                    "autoTruncate": self.config.auto_truncate,
                    "outputDimensionality": self.dimension,
                },
            }
            for text in texts
        ]

    def _extract_vector(self, response: Mapping[str, Any]) -> list[float]:
        try:
            predictions = response["predictions"]
            vector = predictions[0]["embeddings"]["values"]
        except (IndexError, KeyError, TypeError) as exc:
            raise ValueError("Vertex response does not match the embedding contract") from exc
        if not isinstance(vector, list) or len(vector) != self.dimension:
            raise ValueError(f"Vertex embedding dimension mismatch: expected {self.dimension}")
        if any(
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(value)
            for value in vector
        ):
            raise ValueError("Vertex embedding contains a non-finite or non-numeric value")
        return [float(value) for value in vector]

    def embed(self, texts: Sequence[str], *, task_type: str) -> list[list[float]]:
        return [
            self._extract_vector(self.client.predict(request))
            for request in self.build_requests(texts, task_type=task_type)
        ]
