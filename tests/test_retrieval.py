from __future__ import annotations

import pytest

from enterprise_platform.lexical_index import LocalLexicalIndex
from enterprise_platform.retrieval import LocalRetriever, RetrievalMode
from enterprise_platform.vector_store import LocalVectorIndex


def _row(chunk_id: str, text: str, vector: list[float]) -> dict[str, object]:
    return {
        "chunk_id": chunk_id,
        "document_id": f"DOC-{chunk_id.upper()}",
        "chunk_text": text,
        "source_uri": f"synthetic://documents/{chunk_id}",
        "classification": "INTERNAL",
        "allowed_groups": ["support"],
        "embedding_id": f"embedding-{chunk_id}",
        "embedding": vector,
        "embedding_provider": "deterministic-local",
        "embedding_model": "test-model",
        "embedding_dimension": len(vector),
        "embedding_version": "test-model:dimension=2",
        "is_active": True,
    }


def _retriever() -> LocalRetriever:
    rows = [
        _row("combined", "invoice escalation policy", [1.0, 0.0]),
        _row("semantic", "contract notification requirement", [0.9, 0.1]),
        _row("lexical", "invoice invoice overdue review", [0.0, 1.0]),
    ]
    return LocalRetriever(LocalVectorIndex(rows), LocalLexicalIndex(rows))


def test_semantic_lexical_and_hybrid_modes_are_distinct_and_traceable() -> None:
    retriever = _retriever()

    semantic = retriever.search(
        "invoice", query_vector=[1.0, 0.0], mode=RetrievalMode.SEMANTIC, k=3
    )
    lexical = retriever.search("invoice", query_vector=None, mode=RetrievalMode.LEXICAL, k=2)
    hybrid = retriever.search("invoice", query_vector=[1.0, 0.0], mode=RetrievalMode.HYBRID, k=3)

    assert semantic[0].chunk["chunk_id"] == "combined"
    assert semantic[0].semantic_score == 1.0
    assert [hit.chunk["chunk_id"] for hit in lexical] == ["lexical", "combined"]
    assert hybrid[0].chunk["chunk_id"] == "combined"
    assert hybrid[0].semantic_rank == 1
    assert hybrid[0].lexical_rank == 2
    assert hybrid[0].citation_id == "DOC-COMBINED#combined"


def test_hybrid_filter_is_passed_to_both_candidate_generators() -> None:
    retriever = _retriever()

    hits = retriever.search(
        "invoice",
        query_vector=[1.0, 0.0],
        metadata_filter=lambda row: row["chunk_id"] == "lexical",
    )

    assert [hit.chunk["chunk_id"] for hit in hits] == ["lexical"]
    assert hits[0].semantic_rank == hits[0].lexical_rank == 1


def test_empty_result_and_invalid_search_contracts_are_explicit() -> None:
    retriever = _retriever()

    assert (
        retriever.search(
            "missing-term",
            query_vector=None,
            mode=RetrievalMode.LEXICAL,
        )
        == []
    )
    with pytest.raises(ValueError, match="query_vector is required"):
        retriever.search("invoice", query_vector=None, mode=RetrievalMode.HYBRID)
    with pytest.raises(ValueError, match="candidate_k must be at least k"):
        retriever.search("invoice", query_vector=[1.0, 0.0], k=5, candidate_k=4)
    with pytest.raises(ValueError, match="at least one retrieval weight"):
        retriever.search(
            "invoice",
            query_vector=[1.0, 0.0],
            semantic_weight=0,
            lexical_weight=0,
        )
    with pytest.raises(ValueError, match="minimum_score must be finite"):
        retriever.search("invoice", query_vector=[1.0, 0.0], minimum_score=float("nan"))


def test_minimum_score_removes_weak_candidates_before_top_k() -> None:
    hits = _retriever().search(
        "invoice",
        query_vector=[1.0, 0.0],
        mode=RetrievalMode.SEMANTIC,
        k=3,
        minimum_score=0.5,
    )

    assert [hit.chunk["chunk_id"] for hit in hits] == ["combined", "semantic"]
