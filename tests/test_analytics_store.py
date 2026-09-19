from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from data.generate_enterprise_batch import generate_batch
from enterprise_platform.analytics_store import LocalAnalyticsStore
from enterprise_platform.structured import (
    ReplayConflict,
    process_structured_records,
    read_structured_batch,
)


def _result(tmp_path: Path):
    source = generate_batch(
        tmp_path / "input",
        business_date=date(2026, 9, 15),
        batch_id="analytics-001",
        seed=61,
        accounts=3,
    )
    manifest, raw = read_structured_batch(source)
    return process_structured_records(
        raw,
        business_date=date.fromisoformat(manifest["business_date"]),
        batch_id=manifest["batch_id"],
    )


def test_publish_is_transactional_and_builds_account_health_view(tmp_path: Path) -> None:
    result = _result(tmp_path)
    with LocalAnalyticsStore() as store:
        publication = store.publish(result)
        health = store.connection.execute(
            """
            SELECT overdue_invoices, overdue_amount, open_sev1_cases, sla_breaches,
                   api_calls, failed_requests
            FROM serving_account_health
            WHERE account_id = 'A-1001'
            """
        ).fetchone()

    assert publication.inserted_rows == 21
    assert publication.replayed is False
    assert health == (1, 2500, 1, 1, 33000, 448)


def test_identical_replay_is_a_no_op(tmp_path: Path) -> None:
    result = _result(tmp_path)
    with LocalAnalyticsStore() as store:
        first = store.publish(result)
        replay = store.publish(result)
        run_count = store.connection.execute("SELECT count(*) FROM pipeline_runs").fetchone()[0]

    assert first.inserted_rows > 0
    assert replay.replayed is True
    assert replay.inserted_rows == 0
    assert run_count == 1


def test_conflicting_replay_fails_closed_without_mutation(tmp_path: Path) -> None:
    result = _result(tmp_path)
    changed = {entity: list(rows) for entity, rows in result.accepted.items()}
    changed["accounts"][0] = {**changed["accounts"][0], "account_name": "Changed Name"}
    conflict = replace(result, accepted=changed)

    with LocalAnalyticsStore() as store:
        store.publish(result)
        before = store.connection.execute("SELECT count(*) FROM curated_accounts").fetchone()[0]
        with pytest.raises(ReplayConflict, match="different accepted-data fingerprint"):
            store.publish(conflict)
        after = store.connection.execute("SELECT count(*) FROM curated_accounts").fetchone()[0]

    assert before == after == 3
