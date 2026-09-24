from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from google.protobuf import json_format
from google.protobuf.struct_pb2 import Value

from enterprise_platform.vertex_embedding_contract import (
    VertexEmbeddingConfig,
    VertexEmbeddingContract,
)


def protobuf_value(payload: dict[str, Any]) -> Value:
    value = Value()
    json_format.ParseDict(payload, value)
    return value


@dataclass
class FakePredictResponse:
    predictions: list[Value]


class FakePredictionClient:
    def __init__(self, dimension: int) -> None:
        self.dimension = dimension
        self.calls: list[dict[str, Any]] = []

    def predict(
        self,
        *,
        endpoint: str,
        instances: list[Value],
        parameters: Value,
    ) -> FakePredictResponse:
        self.calls.append(
            {"endpoint": endpoint, "instances": list(instances), "parameters": parameters}
        )
        return FakePredictResponse(
            [protobuf_value({"embeddings": {"values": [0.25] * self.dimension}})]
        )


def config(**overrides: Any) -> VertexEmbeddingConfig:
    return VertexEmbeddingConfig(project_id="example-test-project", **overrides)


def test_request_contract_matches_prediction_service_keyword_boundary() -> None:
    client = FakePredictionClient(8)
    provider = VertexEmbeddingContract(client=client, config=config(output_dimension=8))

    vectors = provider.embed(["governed document"], task_type="RETRIEVAL_DOCUMENT")

    assert vectors == [[0.25] * 8]
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["endpoint"] == (
        "projects/example-test-project/locations/us-central1/"
        "publishers/google/models/gemini-embedding-001"
    )
    assert json_format.MessageToDict(call["instances"][0]) == {
        "content": "governed document",
        "task_type": "RETRIEVAL_DOCUMENT",
    }
    assert json_format.MessageToDict(call["parameters"]) == {
        "autoTruncate": False,
        "outputDimensionality": 8.0,
    }


def test_each_text_makes_one_prediction_service_call() -> None:
    client = FakePredictionClient(4)
    provider = VertexEmbeddingContract(client=client, config=config(output_dimension=4))

    vectors = provider.embed(["one", "two"], task_type="RETRIEVAL_QUERY")

    assert vectors == [[0.25] * 4, [0.25] * 4]
    assert len(client.calls) == 2
    assert all(len(call["instances"]) == 1 for call in client.calls)


def test_client_and_configuration_must_be_explicit() -> None:
    client = FakePredictionClient(4)
    with pytest.raises(ValueError, match="injected"):
        VertexEmbeddingContract(client=None, config=config())
    with pytest.raises(ValueError, match="configuration"):
        VertexEmbeddingContract(client=client, config=None)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"project_id": ""}, "project_id"),
        ({"project_id": "bad/project"}, "project_id"),
        ({"project_id": "example-test-project", "location": ""}, "location"),
        (
            {"project_id": "example-test-project", "model_id": "preview-or-legacy-model"},
            "only gemini-embedding-001",
        ),
        ({"project_id": "example-test-project", "output_dimension": 0}, "between 1 and 3072"),
        ({"project_id": "example-test-project", "output_dimension": 3073}, "between 1 and 3072"),
        ({"project_id": "example-test-project", "batch_size": 2}, "one input"),
    ],
)
def test_configuration_fails_closed(kwargs: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        VertexEmbeddingConfig(**kwargs)


def test_invalid_task_type_is_rejected_before_calling_client() -> None:
    client = FakePredictionClient(2)
    provider = VertexEmbeddingContract(client=client, config=config(output_dimension=2))

    with pytest.raises(ValueError, match="unsupported embedding task type"):
        provider.embed(["text"], task_type="UNSUPPORTED")

    assert client.calls == []


class ResponseClient:
    def __init__(self, response: Any) -> None:
        self.response = response

    def predict(self, **_: Any) -> Any:
        return self.response


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (object(), "no predictions"),
        (FakePredictResponse([]), "no predictions"),
        (FakePredictResponse([protobuf_value({"wrong": {}})]), "missing embeddings"),
        (FakePredictResponse([protobuf_value({"embeddings": {}})]), "missing values"),
        (
            FakePredictResponse([protobuf_value({"embeddings": {"values": [0.1]}})]),
            "dimension mismatch",
        ),
        (
            FakePredictResponse([protobuf_value({"embeddings": {"values": [0.1, float("nan")]}})]),
            "non-finite or non-numeric",
        ),
        (
            FakePredictResponse([protobuf_value({"embeddings": {"values": [0.1, float("inf")]}})]),
            "non-finite or non-numeric",
        ),
        (
            FakePredictResponse([protobuf_value({"embeddings": {"values": [0.1, "bad"]}})]),
            "non-finite or non-numeric",
        ),
        (
            {"predictions": [{"embeddings": {"values": [0.1, 0.2]}}]},
            "no predictions",
        ),
    ],
)
def test_malformed_native_responses_fail_closed(response: Any, message: str) -> None:
    provider = VertexEmbeddingContract(
        client=ResponseClient(response),
        config=config(output_dimension=2),
    )

    with pytest.raises(ValueError, match=message):
        provider.embed(["text"], task_type="RETRIEVAL_DOCUMENT")
