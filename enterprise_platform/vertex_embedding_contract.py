"""Statically validated Vertex PredictionService contract with an injected client."""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from google.protobuf import json_format
from google.protobuf.struct_pb2 import Value

from enterprise_platform.embeddings import EMBEDDING_TASK_TYPES, EmbeddingProvider


class VertexPredictionClient(Protocol):
    """Caller-owned boundary matching PredictionServiceClient.predict keyword arguments."""

    def predict(
        self,
        *,
        endpoint: str,
        instances: Sequence[Value],
        parameters: Value,
    ) -> Any: ...


@dataclass(frozen=True)
class VertexPredictCall:
    """A cloud-free representation of one PredictionService invocation."""

    endpoint: str
    instances: list[Value]
    parameters: Value


@dataclass(frozen=True)
class VertexEmbeddingConfig:
    project_id: str
    location: str = "us-central1"
    model_id: str = "gemini-embedding-001"
    output_dimension: int = 3072
    batch_size: int = 1
    auto_truncate: bool = False
    contract_version: str = "vertex-predict-v2"

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]", self.project_id):
            raise ValueError("project_id must be an explicit valid Google Cloud project identifier")
        if not re.fullmatch(r"[a-z][a-z0-9-]*", self.location):
            raise ValueError("location must be an explicit valid Google Cloud location")
        if self.model_id != "gemini-embedding-001":
            raise ValueError("this validated contract supports only gemini-embedding-001")
        if not 1 <= self.output_dimension <= 3072:
            raise ValueError("output_dimension must be between 1 and 3072")
        if self.batch_size != 1:
            raise ValueError("gemini-embedding-001 accepts one input per request")

    @property
    def endpoint(self) -> str:
        return (
            f"projects/{self.project_id}/locations/{self.location}/"
            f"publishers/google/models/{self.model_id}"
        )


def _to_protobuf_value(payload: dict[str, Any]) -> Value:
    value = Value()
    json_format.ParseDict(payload, value)
    return value


class VertexEmbeddingContract(EmbeddingProvider):
    """Build and validate SDK-compatible calls without constructing a cloud client."""

    def __init__(
        self,
        *,
        client: VertexPredictionClient | None,
        config: VertexEmbeddingConfig | None,
    ) -> None:
        if client is None:
            raise ValueError("an explicitly injected Vertex prediction client is required")
        if config is None:
            raise ValueError("an explicit Vertex embedding configuration is required")
        self.client = client
        self.config = config

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

    def build_requests(self, texts: Sequence[str], *, task_type: str) -> list[VertexPredictCall]:
        if task_type not in EMBEDDING_TASK_TYPES:
            raise ValueError(f"unsupported embedding task type: {task_type}")
        parameters = {
            "autoTruncate": self.config.auto_truncate,
            "outputDimensionality": self.dimension,
        }
        return [
            VertexPredictCall(
                endpoint=self.config.endpoint,
                instances=[_to_protobuf_value({"content": text, "task_type": task_type})],
                parameters=_to_protobuf_value(parameters),
            )
            for text in texts
        ]

    def _extract_vector(self, response: Any) -> list[float]:
        predictions = getattr(response, "predictions", None)
        if not predictions:
            raise ValueError("Vertex response contains no predictions")
        prediction = predictions[0]
        if not isinstance(prediction, Value):
            raise ValueError("Vertex prediction must be a protobuf Value")

        try:
            payload = json_format.MessageToDict(prediction)
        except ValueError as exc:
            raise ValueError("Vertex embedding contains a non-finite or non-numeric value") from exc
        if not isinstance(payload, dict) or "embeddings" not in payload:
            raise ValueError("Vertex prediction is missing embeddings")
        embeddings = payload["embeddings"]
        if not isinstance(embeddings, dict) or "values" not in embeddings:
            raise ValueError("Vertex prediction embeddings are missing values")
        vector = embeddings["values"]
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
        vectors: list[list[float]] = []
        for request in self.build_requests(texts, task_type=task_type):
            response = self.client.predict(
                endpoint=request.endpoint,
                instances=request.instances,
                parameters=request.parameters,
            )
            vectors.append(self._extract_vector(response))
        return vectors
