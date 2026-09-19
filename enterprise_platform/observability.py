"""Structured, deterministic local metrics without a monitoring backend."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any


class MetricName(StrEnum):
    INPUT_ROWS = "input_rows"
    ACCEPTED_ROWS = "accepted_rows"
    QUARANTINED_ROWS = "quarantined_rows"
    DOCUMENTS_PROCESSED = "documents_processed"
    CHUNKS_GENERATED = "chunks_generated"
    DOCUMENTS_REPROCESSED = "documents_reprocessed"
    EMBEDDINGS_GENERATED = "embeddings_generated"
    RETRIEVAL_REQUESTS = "retrieval_requests"
    RETRIEVAL_LATENCY_MS = "retrieval_latency_ms"
    RETRIEVAL_EMPTY_RESULTS = "retrieval_empty_results"
    SQL_QUERIES = "sql_queries"
    BLOCKED_SQL_ATTEMPTS = "blocked_sql_attempts"
    ACCESS_DENIED_CANDIDATES = "access_denied_candidates"


_SAFE_DIMENSIONS = frozenset({"asset", "entity", "outcome", "pipeline", "retrieval_method"})


@dataclass(frozen=True)
class MetricPoint:
    name: MetricName
    value: float
    unit: str
    dimensions: tuple[tuple[str, str], ...]


def _dimensions(values: dict[str, str] | None) -> tuple[tuple[str, str], ...]:
    values = values or {}
    unknown = set(values) - _SAFE_DIMENSIONS
    if unknown:
        raise ValueError(f"unsupported metric dimensions: {sorted(unknown)}")
    if any(not key.strip() or not value.strip() for key, value in values.items()):
        raise ValueError("metric dimension names and values cannot be blank")
    return tuple(sorted(values.items()))


class LocalMetricsRegistry:
    """Aggregate bounded-cardinality local counters and explicit latency samples."""

    def __init__(self) -> None:
        self._values: dict[tuple[MetricName, tuple[tuple[str, str], ...]], float] = {}

    def increment(
        self,
        name: MetricName,
        amount: int = 1,
        *,
        dimensions: dict[str, str] | None = None,
    ) -> None:
        if name is MetricName.RETRIEVAL_LATENCY_MS:
            raise ValueError("use observe_retrieval_latency for latency")
        if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
            raise ValueError("counter increments must be non-negative integers")
        key = (name, _dimensions(dimensions))
        self._values[key] = self._values.get(key, 0.0) + amount

    def observe_retrieval_latency(
        self,
        duration_ms: float,
        *,
        retrieval_method: str,
    ) -> None:
        if not math.isfinite(duration_ms) or duration_ms < 0:
            raise ValueError("retrieval latency must be a finite non-negative value")
        key = (
            MetricName.RETRIEVAL_LATENCY_MS,
            _dimensions({"retrieval_method": retrieval_method}),
        )
        self._values[key] = self._values.get(key, 0.0) + duration_ms

    @property
    def snapshot(self) -> tuple[MetricPoint, ...]:
        return tuple(
            MetricPoint(
                name=name,
                value=value,
                unit="milliseconds_total" if name is MetricName.RETRIEVAL_LATENCY_MS else "count",
                dimensions=dimensions,
            )
            for (name, dimensions), value in sorted(
                self._values.items(), key=lambda item: (item[0][0].value, item[0][1])
            )
        )

    def value(self, name: MetricName, *, dimensions: dict[str, str] | None = None) -> float:
        return self._values.get((name, _dimensions(dimensions)), 0.0)

    def export_json(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        records: list[dict[str, Any]] = []
        for point in self.snapshot:
            record = asdict(point)
            record["name"] = point.name.value
            record["dimensions"] = dict(point.dimensions)
            records.append(record)
        payload = json.dumps(records, sort_keys=True, indent=2, allow_nan=False) + "\n"
        staging = path.with_suffix(path.suffix + ".tmp")
        staging.write_text(payload, encoding="utf-8", newline="\n")
        staging.replace(path)
        return path
