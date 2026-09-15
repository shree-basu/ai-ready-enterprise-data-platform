from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from data.generate_enterprise_batch import generate_batch
from enterprise_platform.analytics_store import LocalAnalyticsStore
from enterprise_platform.freshness import (
    FreshnessState,
    FreshnessThresholds,
    evaluate_freshness,
    latest_analytics_update,
    latest_document_update,
)
from enterprise_platform.structured import process_structured_records, read_structured_batch


def test_fresh_sources_are_safe_for_answer_at_the_inclusive_boundary() -> None:
    evaluated_at = datetime(2026, 9, 15, 12, tzinfo=UTC)
    report = evaluate_freshness(
        evaluated_at=evaluated_at,
        analytics_observed_at=evaluated_at - timedelta(hours=36),
        documents_observed_at=evaluated_at - timedelta(hours=12),
    )

    assert report.state is FreshnessState.FRESH
    assert report.safe_for_answer is True
    assert report.discrepancies == ()


def test_staleness_and_cross_source_skew_are_both_exposed() -> None:
    evaluated_at = datetime(2026, 9, 15, 12, tzinfo=UTC)
    report = evaluate_freshness(
        evaluated_at=evaluated_at,
        analytics_observed_at=evaluated_at - timedelta(days=4),
        documents_observed_at=evaluated_at - timedelta(hours=1),
    )

    assert report.state is FreshnessState.STALE
    assert report.safe_for_answer is False
    assert report.discrepancies == (
        "analytics freshness is STALE",
        "analytics and document timestamps exceed the permitted source skew",
    )


def test_missing_future_and_naive_timestamps_fail_closed() -> None:
    evaluated_at = datetime(2026, 9, 15, 12, tzinfo=UTC)
    missing = evaluate_freshness(
        evaluated_at=evaluated_at,
        analytics_observed_at=None,
        documents_observed_at=None,
    )
    future = evaluate_freshness(
        evaluated_at=evaluated_at,
        analytics_observed_at=evaluated_at + timedelta(minutes=1),
        documents_observed_at=evaluated_at,
    )

    assert missing.state is FreshnessState.MISSING
    assert future.state is FreshnessState.FUTURE
    with pytest.raises(ValueError, match="evaluated_at must include a timezone"):
        evaluate_freshness(
            evaluated_at=datetime(2026, 9, 15, 12),
            analytics_observed_at=None,
            documents_observed_at=None,
        )


def test_thresholds_cannot_be_negative() -> None:
    with pytest.raises(ValueError, match="cannot be negative"):
        FreshnessThresholds(analytics_max_age=timedelta(seconds=-1))


def test_local_serving_and_document_rows_supply_observed_timestamps(tmp_path: Path) -> None:
    source = generate_batch(
        tmp_path / "input",
        business_date=date(2026, 9, 15),
        batch_id="freshness-001",
        seed=62,
        accounts=2,
    )
    manifest, raw = read_structured_batch(source)
    result = process_structured_records(
        raw,
        business_date=date.fromisoformat(manifest["business_date"]),
        batch_id=manifest["batch_id"],
    )
    with LocalAnalyticsStore() as store:
        assert latest_analytics_update(store.connection) is None
        store.publish(result)
        analytics_update = latest_analytics_update(store.connection)

    document_update = latest_document_update([{"document_updated_at": "2026-09-15T02:00:00Z"}])
    assert analytics_update == datetime(2026, 9, 15, 1, 35, tzinfo=UTC)
    assert document_update == datetime(2026, 9, 15, 2, tzinfo=UTC)
