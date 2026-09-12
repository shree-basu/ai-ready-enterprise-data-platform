from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from enterprise_platform.vertex_embedding_contract import (
    VertexEmbeddingConfig,
    VertexEmbeddingContract,
)


class FakePredictionClient:
    def __init__(self, dimension: int) -> None:
        self.dimension = dimension
        self.requests: list[Mapping[str, Any]] = []

    def predict(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        self.requests.append(request)
        return {"predictions": [{"embeddings": {"values": [0.25] * self.dimension}}]}


def test_request_contract_uses_stable_model_task_dimension_and_no_truncation() -> None:
    client = FakePredictionClient(8)
    provider = VertexEmbeddingContract(
        client=client,
        config=VertexEmbeddingConfig(output_dimension=8),
    )

    vectors = provider.embed(["governed document"], task_type="RETRIEVAL_DOCUMENT")

    assert vectors == [[0.25] * 8]
    assert client.requests == [
        {
            "model": "gemini-embedding-001",
            "instances": [
                {
                    "content": "governed document",
                    "task_type": "RETRIEVAL_DOCUMENT",
                }
            ],
            "parameters": {
                "autoTruncate": False,
                "outputDimensionality": 8,
            },
        }
    ]


def test_each_text_builds_one_request_for_the_validated_model_contract() -> None:
    client = FakePredictionClient(4)
    provider = VertexEmbeddingContract(
        client=client,
        config=VertexEmbeddingConfig(output_dimension=4),
    )

    vectors = provider.embed(["one", "two"], task_type="RETRIEVAL_QUERY")

    assert len(vectors) == 2
    assert len(client.requests) == 2


def test_client_must_be_injected_and_configuration_fails_closed() -> None:
    with pytest.raises(ValueError, match="injected"):
        VertexEmbeddingContract(client=None)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="only gemini-embedding-001"):
        VertexEmbeddingConfig(model_id="preview-or-legacy-model")
    with pytest.raises(ValueError, match="one input"):
        VertexEmbeddingConfig(batch_size=2)


class InvalidResponseClient:
    def __init__(self, vector: list[Any]) -> None:
        self.vector = vector

    def predict(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        return {"predictions": [{"embeddings": {"values": self.vector}}]}


@pytest.mark.parametrize("vector", [[0.1], [0.1, float("nan")], [0.1, "bad"]])
def test_invalid_response_dimensions_and_values_are_rejected(vector: list[Any]) -> None:
    provider = VertexEmbeddingContract(
        client=InvalidResponseClient(vector),
        config=VertexEmbeddingConfig(output_dimension=2),
    )

    with pytest.raises(ValueError, match="dimension mismatch|non-finite|non-numeric"):
        provider.embed(["text"], task_type="RETRIEVAL_DOCUMENT")
