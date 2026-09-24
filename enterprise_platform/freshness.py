"""Deterministic freshness evaluation across analytics and governed documents."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

import duckdb


class FreshnessState(StrEnum):
    FRESH = "FRESH"
    STALE = "STALE"
    MISSING = "MISSING"
    FUTURE = "FUTURE"


@dataclass(frozen=True)
class FreshnessThresholds:
    analytics_max_age: timedelta = timedelta(hours=36)
    documents_max_age: timedelta = timedelta(hours=36)
    max_source_skew: timedelta = timedelta(hours=24)

    def __post_init__(self) -> None:
        configured = (self.analytics_max_age, self.documents_max_age, self.max_source_skew)
        if min(configured) < timedelta():
            raise ValueError("freshness thresholds cannot be negative")


@dataclass(frozen=True)
class SourceFreshness:
    source: str
    state: FreshnessState
    observed_at: datetime | None
    age: timedelta | None
    max_age: timedelta


@dataclass(frozen=True)
class FreshnessReport:
    state: FreshnessState
    evaluated_at: datetime
    analytics: SourceFreshness
    documents: SourceFreshness
    discrepancies: tuple[str, ...]

    @property
    def safe_for_answer(self) -> bool:
        return self.state is FreshnessState.FRESH


def _aware_utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone")
    return value.astimezone(UTC)


def _source_freshness(
    source: str,
    observed_at: datetime | None,
    *,
    evaluated_at: datetime,
    max_age: timedelta,
) -> SourceFreshness:
    if observed_at is None:
        return SourceFreshness(source, FreshnessState.MISSING, None, None, max_age)
    observed = _aware_utc(observed_at, f"{source}_observed_at")
    age = evaluated_at - observed
    if age < timedelta():
        state = FreshnessState.FUTURE
    elif age <= max_age:
        state = FreshnessState.FRESH
    else:
        state = FreshnessState.STALE
    return SourceFreshness(source, state, observed, age, max_age)


def evaluate_freshness(
    *,
    evaluated_at: datetime,
    analytics_observed_at: datetime | None,
    documents_observed_at: datetime | None,
    thresholds: FreshnessThresholds | None = None,
) -> FreshnessReport:
    """Evaluate both sources without reading the wall clock or hiding discrepancies."""

    thresholds = thresholds or FreshnessThresholds()
    evaluated = _aware_utc(evaluated_at, "evaluated_at")
    analytics = _source_freshness(
        "analytics",
        analytics_observed_at,
        evaluated_at=evaluated,
        max_age=thresholds.analytics_max_age,
    )
    documents = _source_freshness(
        "documents",
        documents_observed_at,
        evaluated_at=evaluated,
        max_age=thresholds.documents_max_age,
    )

    discrepancies = [
        f"{source.source} freshness is {source.state.value}"
        for source in (analytics, documents)
        if source.state is not FreshnessState.FRESH
    ]
    if analytics.observed_at is not None and documents.observed_at is not None:
        skew = abs(analytics.observed_at - documents.observed_at)
        if skew > thresholds.max_source_skew:
            discrepancies.append(
                "analytics and document timestamps exceed the permitted source skew"
            )

    states = {analytics.state, documents.state}
    if FreshnessState.FUTURE in states:
        state = FreshnessState.FUTURE
    elif FreshnessState.MISSING in states:
        state = FreshnessState.MISSING
    elif FreshnessState.STALE in states:
        state = FreshnessState.STALE
    else:
        state = FreshnessState.FRESH
    return FreshnessReport(
        state=state,
        evaluated_at=evaluated,
        analytics=analytics,
        documents=documents,
        discrepancies=tuple(discrepancies),
    )


def latest_analytics_update(connection: duckdb.DuckDBPyConnection) -> datetime | None:
    row = connection.execute("SELECT data_updated_at FROM analytics_freshness").fetchone()
    return None if row is None or row[0] is None else _aware_utc(row[0], "data_updated_at")


def latest_document_update(rows: Iterable[dict[str, Any]]) -> datetime | None:
    observed: list[datetime] = []
    for row in rows:
        value = row.get("document_updated_at")
        if value is None:
            continue
        if isinstance(value, str):
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if not isinstance(value, datetime):
            raise ValueError("document_updated_at must be a datetime or ISO-8601 string")
        observed.append(_aware_utc(value, "document_updated_at"))
    return max(observed, default=None)
