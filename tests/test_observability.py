from __future__ import annotations

import json
from pathlib import Path

import pytest

from enterprise_platform.observability import LocalMetricsRegistry, MetricName


def test_pipeline_agent_and_retrieval_metrics_are_structured_and_replayable(tmp_path: Path) -> None:
    metrics = LocalMetricsRegistry()
    metrics.increment(MetricName.INPUT_ROWS, 10, dimensions={"entity": "invoices"})
    metrics.increment(MetricName.ACCEPTED_ROWS, 8, dimensions={"entity": "invoices"})
    metrics.increment(MetricName.QUARANTINED_ROWS, 2, dimensions={"entity": "invoices"})
    metrics.increment(MetricName.DOCUMENTS_PROCESSED, 3)
    metrics.increment(MetricName.CHUNKS_GENERATED, 7)
    metrics.increment(MetricName.DOCUMENTS_REPROCESSED, 1)
    metrics.increment(MetricName.EMBEDDINGS_GENERATED, 7)
    metrics.observe_retrieval_latency(12.5, retrieval_method="hybrid")
    metrics.observe_retrieval_latency(7.5, retrieval_method="hybrid")

    assert metrics.value(MetricName.INPUT_ROWS, dimensions={"entity": "invoices"}) == 10
    assert (
        metrics.value(
            MetricName.RETRIEVAL_LATENCY_MS,
            dimensions={"retrieval_method": "hybrid"},
        )
        == 20
    )
    first = metrics.export_json(tmp_path / "metrics.json").read_bytes()
    second = metrics.export_json(tmp_path / "metrics.json").read_bytes()
    records = json.loads(first)

    assert first == second
    assert {record["name"] for record in records} >= {
        "input_rows",
        "accepted_rows",
        "quarantined_rows",
        "retrieval_latency_ms",
    }


def test_metrics_reject_sensitive_or_unbounded_dimensions_and_invalid_values() -> None:
    metrics = LocalMetricsRegistry()
    with pytest.raises(ValueError, match="unsupported metric dimensions"):
        metrics.increment(MetricName.SQL_QUERIES, dimensions={"user_id": "personal-value"})
    with pytest.raises(ValueError, match="non-negative integers"):
        metrics.increment(MetricName.INPUT_ROWS, -1)
    with pytest.raises(ValueError, match="finite non-negative"):
        metrics.observe_retrieval_latency(float("nan"), retrieval_method="hybrid")
    with pytest.raises(ValueError, match="use observe"):
        metrics.increment(MetricName.RETRIEVAL_LATENCY_MS)
