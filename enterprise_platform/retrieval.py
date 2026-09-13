"""Deterministic semantic, lexical, and hybrid retrieval orchestration."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from enterprise_platform.lexical_index import LocalLexicalIndex, MetadataFilter
from enterprise_platform.vector_store import LocalVectorIndex


class RetrievalMode(StrEnum):
    SEMANTIC = "semantic"
    LEXICAL = "lexical"
    HYBRID = "hybrid"


@dataclass(frozen=True)
class RetrievalHit:
    chunk: dict[str, Any]
    score: float
    semantic_score: float | None
    lexical_score: float | None
    semantic_rank: int | None
    lexical_rank: int | None

    @property
    def citation_id(self) -> str:
        return f"{self.chunk['document_id']}#{self.chunk['chunk_id']}"


class LocalRetriever:
    """Coordinate local indices and combine ranks without comparing raw score scales."""

    def __init__(
        self,
        vector_index: LocalVectorIndex,
        lexical_index: LocalLexicalIndex,
        *,
        reciprocal_rank_constant: int = 60,
    ) -> None:
        if reciprocal_rank_constant < 1:
            raise ValueError("reciprocal_rank_constant must be positive")
        self.vector_index = vector_index
        self.lexical_index = lexical_index
        self.reciprocal_rank_constant = reciprocal_rank_constant

    def search(
        self,
        query: str,
        *,
        query_vector: Sequence[float] | None,
        mode: RetrievalMode = RetrievalMode.HYBRID,
        k: int = 5,
        candidate_k: int = 20,
        metadata_filter: MetadataFilter | None = None,
        semantic_weight: float = 1.0,
        lexical_weight: float = 1.0,
    ) -> list[RetrievalHit]:
        if k < 1:
            raise ValueError("k must be positive")
        if candidate_k < k:
            raise ValueError("candidate_k must be at least k")
        if semantic_weight < 0 or lexical_weight < 0:
            raise ValueError("retrieval weights cannot be negative")
        if semantic_weight == lexical_weight == 0:
            raise ValueError("at least one retrieval weight must be positive")
        if mode in {RetrievalMode.SEMANTIC, RetrievalMode.HYBRID} and query_vector is None:
            raise ValueError(f"query_vector is required for {mode.value} retrieval")

        semantic_hits = (
            self.vector_index.search(
                query_vector or (),
                k=candidate_k,
                metadata_filter=metadata_filter,
            )
            if mode in {RetrievalMode.SEMANTIC, RetrievalMode.HYBRID}
            else []
        )
        lexical_hits = (
            self.lexical_index.search(
                query,
                k=candidate_k,
                metadata_filter=metadata_filter,
            )
            if mode in {RetrievalMode.LEXICAL, RetrievalMode.HYBRID}
            else []
        )

        semantic_by_id = {
            str(hit.chunk["chunk_id"]): (rank, hit)
            for rank, hit in enumerate(semantic_hits, start=1)
        }
        lexical_by_id = {
            str(hit.chunk["chunk_id"]): (rank, hit)
            for rank, hit in enumerate(lexical_hits, start=1)
        }
        chunk_ids = set(semantic_by_id) | set(lexical_by_id)
        combined: list[RetrievalHit] = []
        for chunk_id in chunk_ids:
            semantic = semantic_by_id.get(chunk_id)
            lexical = lexical_by_id.get(chunk_id)
            semantic_rank, semantic_hit = semantic if semantic is not None else (None, None)
            lexical_rank, lexical_hit = lexical if lexical is not None else (None, None)
            chunk = semantic_hit.chunk if semantic_hit is not None else lexical_hit.chunk

            if mode == RetrievalMode.SEMANTIC:
                score = semantic_hit.score
            elif mode == RetrievalMode.LEXICAL:
                score = lexical_hit.score
            else:
                score = 0.0
                if semantic_rank is not None:
                    score += semantic_weight / (self.reciprocal_rank_constant + semantic_rank)
                if lexical_rank is not None:
                    score += lexical_weight / (self.reciprocal_rank_constant + lexical_rank)
            combined.append(
                RetrievalHit(
                    chunk=dict(chunk),
                    score=score,
                    semantic_score=None if semantic_hit is None else semantic_hit.score,
                    lexical_score=None if lexical_hit is None else lexical_hit.score,
                    semantic_rank=semantic_rank,
                    lexical_rank=lexical_rank,
                )
            )
        return sorted(combined, key=lambda hit: (-hit.score, hit.chunk["chunk_id"]))[:k]
