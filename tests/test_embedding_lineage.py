from __future__ import annotations

from datetime import UTC, datetime

import pytest

from enterprise_platform.embedding_lineage import (
    EmbeddingProcessingError,
    EmbeddingSpaceMismatch,
    assert_compatible_embedding_space,
    embed_chunks,
    plan_reindex,
)
from enterprise_platform.embeddings import (
    DeterministicLocalEmbeddingProvider,
    EmbeddingProvider,
)

EMBEDDED_AT = datetime(2026, 9, 12, tzinfo=UTC)


def _chunk(chunk_id: str = "chunk-1") -> dict[str, object]:
    return {
        "chunk_id": chunk_id,
        "chunk_text": "SEV1 API incident recovery procedure",
        "classification": "INTERNAL",
        "allowed_groups": ["sre", "platform"],
        "document_id": "DOC-RUNBOOK-001",
    }


class _BrokenProvider(EmbeddingProvider):
    @property
    def provider_name(self) -> str:
        return "broken"

    @property
    def model_id(self) -> str:
        return "broken-v1"

    @property
    def dimension(self) -> int:
        return 8

    @property
    def embedding_version(self) -> str:
        return "broken-v1:dimension=8"

    def embed(self, texts, *, task_type):
        return [[1.0, 2.0] for _ in texts]


def test_embedding_lineage_is_deterministic_and_preserves_governance() -> None:
    provider = DeterministicLocalEmbeddingProvider(dimension=16)

    first = embed_chunks([_chunk()], provider, embedded_at=EMBEDDED_AT)
    second = embed_chunks([_chunk()], provider, embedded_at=EMBEDDED_AT)

    assert first == second
    assert first[0]["classification"] == "INTERNAL"
    assert first[0]["allowed_groups"] == ["sre", "platform"]
    assert first[0]["embedding_dimension"] == 16
    assert first[0]["embedding_version"] == "token-hash-embedding-v1:dimension=16"
    assert first[0]["embedded_at"] == "2026-09-12T00:00:00+00:00"


def test_provider_dimension_mismatch_fails_closed() -> None:
    with pytest.raises(EmbeddingProcessingError, match="dimension mismatch"):
        embed_chunks([_chunk()], _BrokenProvider(), embedded_at=EMBEDDED_AT)


def test_provider_failure_is_wrapped_without_exposing_chunk_text() -> None:
    class FailingProvider(_BrokenProvider):
        def embed(self, texts, *, task_type):
            raise RuntimeError("secret provider payload")

    with pytest.raises(EmbeddingProcessingError, match="embedding provider failed") as exc:
        embed_chunks([_chunk()], FailingProvider(), embedded_at=EMBEDDED_AT)

    assert "SEV1" not in str(exc.value)
    assert "secret" not in str(exc.value)


def test_mixed_embedding_spaces_are_rejected() -> None:
    first = embed_chunks(
        [_chunk("chunk-1")],
        DeterministicLocalEmbeddingProvider(dimension=8),
        embedded_at=EMBEDDED_AT,
    )[0]
    second = embed_chunks(
        [_chunk("chunk-2")],
        DeterministicLocalEmbeddingProvider(dimension=16),
        embedded_at=EMBEDDED_AT,
    )[0]

    with pytest.raises(EmbeddingSpaceMismatch, match="mixed embedding spaces"):
        assert_compatible_embedding_space([first, second])


def test_reindex_is_required_for_missing_or_changed_embedding_contract() -> None:
    old_provider = DeterministicLocalEmbeddingProvider(dimension=8)
    new_provider = DeterministicLocalEmbeddingProvider(dimension=16)
    old = embed_chunks([_chunk("old")], old_provider, embedded_at=EMBEDDED_AT)[0]
    current = embed_chunks([_chunk("current")], new_provider, embedded_at=EMBEDDED_AT)[0]
    missing = _chunk("missing")

    assert plan_reindex([old, current, missing], new_provider) == ["old", "missing"]
    assert_compatible_embedding_space([current], provider=new_provider)


def test_naive_embedding_timestamp_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        embed_chunks(
            [_chunk()],
            DeterministicLocalEmbeddingProvider(),
            embedded_at=datetime(2026, 9, 12),
        )
