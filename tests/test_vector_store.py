from __future__ import annotations

import pytest

from enterprise_platform.embedding_lineage import EmbeddingSpaceMismatch
from enterprise_platform.vector_store import LocalVectorIndex


def _row(
    chunk_id: str,
    vector: list[float],
    *,
    active: bool = True,
    version: str = "local-v1",
) -> dict[str, object]:
    return {
        "chunk_id": chunk_id,
        "embedding_id": f"embedding-{chunk_id}-{version}",
        "chunk_text": f"text for {chunk_id}",
        "embedding": vector,
        "embedding_provider": "deterministic-local",
        "embedding_model": "test-model",
        "embedding_dimension": len(vector),
        "embedding_version": version,
        "is_active": active,
    }


def test_exact_cosine_search_is_deterministic_and_ignores_inactive_rows() -> None:
    index = LocalVectorIndex(
        [
            _row("matching", [1.0, 0.0]),
            _row("partial", [0.5, 0.5]),
            _row("opposite", [-1.0, 0.0]),
            _row("inactive", [1.0, 0.0], active=False),
        ]
    )

    hits = index.search([1.0, 0.0], k=3)

    assert [hit.chunk["chunk_id"] for hit in hits] == ["matching", "partial", "opposite"]
    assert [round(hit.score, 6) for hit in hits] == [1.0, 0.707107, -1.0]
    assert index.size == 3


def test_publication_is_replay_safe_and_conflicts_are_atomic() -> None:
    row = _row("one", [1.0, 0.0])
    index = LocalVectorIndex([row])

    replay = index.add([row])
    assert (replay.inserted, replay.replayed) == (0, 1)

    conflicting = {**row, "chunk_text": "mutated without a new identity"}
    with pytest.raises(ValueError, match="conflicting active embedding"):
        index.add([_row("two", [0.0, 1.0]), conflicting])
    assert index.size == 1


def test_dimension_space_and_numeric_errors_fail_closed() -> None:
    index = LocalVectorIndex([_row("one", [1.0, 0.0])])

    with pytest.raises(EmbeddingSpaceMismatch, match="query dimension mismatch"):
        index.search([1.0])
    with pytest.raises(ValueError, match="finite numeric"):
        index.search([float("nan"), 0.0])
    with pytest.raises(EmbeddingSpaceMismatch, match="indexed embedding space"):
        index.add([_row("new-space", [0.0, 1.0], version="local-v2")])


def test_metadata_filter_runs_before_ranking_and_empty_queries_return_no_result() -> None:
    index = LocalVectorIndex([_row("restricted", [1.0, 0.0]), _row("allowed", [0.8, 0.2])])

    hits = index.search(
        [1.0, 0.0],
        metadata_filter=lambda row: row["chunk_id"] == "allowed",
    )

    assert [hit.chunk["chunk_id"] for hit in hits] == ["allowed"]
    assert index.search([0.0, 0.0]) == []
