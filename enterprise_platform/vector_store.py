"""Cloud-free exact vector index with replay-safe in-memory publication."""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from enterprise_platform.embedding_lineage import (
    EmbeddingSpaceMismatch,
    assert_compatible_embedding_space,
)


@dataclass(frozen=True)
class IndexUpdate:
    inserted: int
    replayed: int
    ignored_inactive: int


@dataclass(frozen=True)
class VectorHit:
    chunk: dict[str, Any]
    score: float


MetadataFilter = Callable[[dict[str, Any]], bool]


class LocalVectorIndex:
    """A deterministic exact cosine index; it is not a production ANN service."""

    def __init__(self, rows: Iterable[dict[str, Any]] = ()) -> None:
        self._rows: dict[str, dict[str, Any]] = {}
        self._space: tuple[str, str, int, str] | None = None
        self.add(rows)

    @staticmethod
    def _lineage(row: dict[str, Any]) -> tuple[str, str, int, str]:
        try:
            return (
                str(row["embedding_provider"]),
                str(row["embedding_model"]),
                int(row["embedding_dimension"]),
                str(row["embedding_version"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise EmbeddingSpaceMismatch("embedding lineage is incomplete") from exc

    def add(self, rows: Iterable[dict[str, Any]]) -> IndexUpdate:
        incoming = [dict(row) for row in rows]
        active = [row for row in incoming if row.get("is_active", True)]
        ignored = len(incoming) - len(active)
        if not active:
            return IndexUpdate(inserted=0, replayed=0, ignored_inactive=ignored)

        assert_compatible_embedding_space(active)
        incoming_space = self._lineage(active[0])
        if self._space is not None and incoming_space != self._space:
            raise EmbeddingSpaceMismatch("incoming rows do not match the indexed embedding space")

        staged = dict(self._rows)
        inserted = 0
        replayed = 0
        for row in active:
            chunk_id = str(row["chunk_id"])
            existing = staged.get(chunk_id)
            if existing is None:
                staged[chunk_id] = row
                inserted += 1
            elif existing == row:
                replayed += 1
            else:
                raise ValueError(f"conflicting active embedding for chunk_id={chunk_id}")
        self._rows = staged
        self._space = incoming_space
        return IndexUpdate(inserted=inserted, replayed=replayed, ignored_inactive=ignored)

    @property
    def size(self) -> int:
        return len(self._rows)

    @property
    def embedding_dimension(self) -> int | None:
        return None if self._space is None else self._space[2]

    @property
    def rows(self) -> list[dict[str, Any]]:
        return [dict(self._rows[chunk_id]) for chunk_id in sorted(self._rows)]

    def search(
        self,
        query_vector: Sequence[float],
        *,
        k: int = 5,
        metadata_filter: MetadataFilter | None = None,
    ) -> list[VectorHit]:
        """Return exact cosine matches after applying the caller's metadata filter."""

        if k < 1:
            raise ValueError("k must be positive")
        if self._space is None:
            return []
        if len(query_vector) != self._space[2]:
            raise EmbeddingSpaceMismatch(
                f"query dimension mismatch: expected {self._space[2]}, received {len(query_vector)}"
            )
        if any(
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(value)
            for value in query_vector
        ):
            raise ValueError("query vector must contain only finite numeric values")
        query = [float(value) for value in query_vector]
        query_norm = math.sqrt(sum(value * value for value in query))
        if query_norm == 0:
            return []

        hits: list[VectorHit] = []
        for chunk_id in sorted(self._rows):
            row = self._rows[chunk_id]
            if metadata_filter is not None and not metadata_filter(row):
                continue
            vector = row["embedding"]
            vector_norm = math.sqrt(sum(value * value for value in vector))
            if vector_norm == 0:
                continue
            score = sum(
                query_value * vector_value
                for query_value, vector_value in zip(query, vector, strict=True)
            ) / (query_norm * vector_norm)
            hits.append(VectorHit(chunk=dict(row), score=score))
        return sorted(hits, key=lambda hit: (-hit.score, hit.chunk["chunk_id"]))[:k]
