"""Embedding lineage, compatibility, and explicit reindex boundaries."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

from enterprise_platform.embeddings import EmbeddingProvider


class EmbeddingProcessingError(RuntimeError):
    """Raised when a provider cannot produce a valid embedding batch."""


class EmbeddingSpaceMismatch(ValueError):
    """Raised when vectors from incompatible embedding spaces are combined."""


def _lineage(provider: EmbeddingProvider) -> tuple[str, str, int, str]:
    if provider.dimension < 1:
        raise EmbeddingProcessingError("embedding provider dimension must be positive")
    return (
        provider.provider_name,
        provider.model_id,
        provider.dimension,
        provider.embedding_version,
    )


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("embedded_at must be timezone-aware")
    return value.isoformat()


def _embedding_id(chunk_id: str, lineage: tuple[str, str, int, str]) -> str:
    provider_name, model_id, dimension, version = lineage
    identity = "\x1f".join((chunk_id, provider_name, model_id, str(dimension), version))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _validated_vector(vector: Sequence[float], *, dimension: int) -> list[float]:
    if len(vector) != dimension:
        raise EmbeddingProcessingError(
            f"embedding dimension mismatch: expected {dimension}, received {len(vector)}"
        )
    values: list[float] = []
    for value in vector:
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise EmbeddingProcessingError("embedding contains a non-numeric value")
        converted = float(value)
        if not math.isfinite(converted):
            raise EmbeddingProcessingError("embedding contains a non-finite value")
        values.append(converted)
    return values


def embed_chunks(
    chunks: Sequence[dict[str, Any]],
    provider: EmbeddingProvider,
    *,
    embedded_at: datetime,
) -> list[dict[str, Any]]:
    """Embed a chunk batch and attach deterministic, auditable model lineage."""

    timestamp = _timestamp(embedded_at)
    lineage = _lineage(provider)
    try:
        vectors = provider.embed(
            [str(chunk["chunk_text"]) for chunk in chunks],
            task_type="RETRIEVAL_DOCUMENT",
        )
    except Exception as exc:
        raise EmbeddingProcessingError("embedding provider failed") from exc
    if len(vectors) != len(chunks):
        raise EmbeddingProcessingError(
            f"embedding count mismatch: expected {len(chunks)}, received {len(vectors)}"
        )

    provider_name, model_id, dimension, version = lineage
    embedded: list[dict[str, Any]] = []
    for chunk, vector in zip(chunks, vectors, strict=True):
        chunk_id = str(chunk["chunk_id"])
        embedded.append(
            {
                **chunk,
                "embedding_id": _embedding_id(chunk_id, lineage),
                "embedding": _validated_vector(vector, dimension=dimension),
                "embedding_provider": provider_name,
                "embedding_model": model_id,
                "embedding_dimension": dimension,
                "embedding_version": version,
                "embedded_at": timestamp,
            }
        )
    return embedded


def assert_compatible_embedding_space(
    rows: Iterable[dict[str, Any]],
    *,
    provider: EmbeddingProvider | None = None,
) -> None:
    """Reject mixed or malformed vector spaces before indexing or retrieval."""

    expected = _lineage(provider) if provider is not None else None
    observed: set[tuple[str, str, int, str]] = set()
    for row in rows:
        try:
            lineage = (
                str(row["embedding_provider"]),
                str(row["embedding_model"]),
                int(row["embedding_dimension"]),
                str(row["embedding_version"]),
            )
            vector = row["embedding"]
        except (KeyError, TypeError, ValueError) as exc:
            raise EmbeddingSpaceMismatch("embedding lineage is incomplete") from exc
        try:
            _validated_vector(vector, dimension=lineage[2])
        except EmbeddingProcessingError as exc:
            raise EmbeddingSpaceMismatch(str(exc)) from exc
        observed.add(lineage)
    if len(observed) > 1:
        raise EmbeddingSpaceMismatch("mixed embedding spaces are not compatible")
    if expected is not None and observed and observed != {expected}:
        raise EmbeddingSpaceMismatch("stored embeddings do not match the requested provider")


def plan_reindex(chunks: Iterable[dict[str, Any]], provider: EmbeddingProvider) -> list[str]:
    """Return chunk IDs missing the exact requested provider/model/version contract."""

    expected = _lineage(provider)
    pending: list[str] = []
    for chunk in chunks:
        actual = (
            chunk.get("embedding_provider"),
            chunk.get("embedding_model"),
            chunk.get("embedding_dimension"),
            chunk.get("embedding_version"),
        )
        vector = chunk.get("embedding")
        valid_vector = isinstance(vector, Sequence) and not isinstance(vector, str | bytes)
        if actual != expected or not valid_vector or len(vector) != provider.dimension:
            pending.append(str(chunk["chunk_id"]))
    return pending
