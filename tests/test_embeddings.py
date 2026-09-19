from __future__ import annotations

import math

import pytest

from enterprise_platform.embeddings import DeterministicLocalEmbeddingProvider


def _cosine(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))


def test_local_embeddings_are_deterministic_fixed_length_and_normalized() -> None:
    provider = DeterministicLocalEmbeddingProvider(dimension=32)
    texts = ["SEV1 API availability incident", "invoice payment overdue"]

    first = provider.embed(texts, task_type="RETRIEVAL_DOCUMENT")
    second = provider.embed(texts, task_type="RETRIEVAL_DOCUMENT")

    assert first == second
    assert all(len(vector) == 32 for vector in first)
    assert all(math.isclose(_cosine(vector, vector), 1.0) for vector in first)
    assert provider.embedding_version == "token-hash-embedding-v1:dimension=32"


def test_query_and_document_vectors_share_comparable_local_space() -> None:
    provider = DeterministicLocalEmbeddingProvider(dimension=64)
    document = provider.embed(
        ["API availability incident runbook"], task_type="RETRIEVAL_DOCUMENT"
    )[0]
    matching_query = provider.embed(["availability incident API"], task_type="RETRIEVAL_QUERY")[0]
    unrelated_query = provider.embed(["quarterly invoice currency"], task_type="RETRIEVAL_QUERY")[0]

    assert _cosine(document, matching_query) > _cosine(document, unrelated_query)


def test_empty_text_has_a_stable_zero_vector() -> None:
    provider = DeterministicLocalEmbeddingProvider(dimension=16)

    assert provider.embed([""], task_type="RETRIEVAL_DOCUMENT") == [[0.0] * 16]


def test_invalid_dimension_and_task_type_fail_closed() -> None:
    with pytest.raises(ValueError, match="at least 8"):
        DeterministicLocalEmbeddingProvider(dimension=4)

    provider = DeterministicLocalEmbeddingProvider()
    with pytest.raises(ValueError, match="unsupported embedding task type"):
        provider.embed(["text"], task_type="UNSAFE_TASK")
