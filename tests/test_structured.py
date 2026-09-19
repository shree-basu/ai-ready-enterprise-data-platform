from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from data.generate_enterprise_batch import generate_batch
from enterprise_platform.structured import ReplayConflict, publish_structured_batch


def _source(tmp_path: Path, scenario: str = "normal") -> Path:
    return generate_batch(
        tmp_path / "input",
        business_date=date(2026, 9, 10),
        batch_id="batch-002",
        seed=51,
        accounts=4,
        scenario=scenario,
    )


def _summary(output: Path) -> dict:
    return json.loads((output / "reconciliation.json").read_text(encoding="utf-8"))


def test_normal_batch_reconciles_and_records_deterministic_lineage(tmp_path: Path) -> None:
    output = publish_structured_batch(_source(tmp_path), tmp_path / "output")
    summary = _summary(output)
    account = json.loads(
        (output / "accepted/accounts.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )

    assert all(
        counts["source_rows"] == counts["accepted_rows"] + counts["quarantined_rows"]
        for counts in summary["entities"].values()
    )
    assert account["_lineage"]["batch_id"] == "batch-002"
    assert account["_lineage"]["structured_row_id"]
    assert (output / "_SUCCESS").read_bytes() == b""


@pytest.mark.parametrize(
    ("scenario", "entity", "code"),
    [
        ("duplicate-key", "accounts", "DUPLICATE_NATURAL_KEY"),
        ("orphan-reference", "invoices", "ORPHAN_ACCOUNT_REFERENCE"),
        ("invalid-domain", "accounts", "CONTRACT_VIOLATION"),
        ("stale-data", "accounts", "CONTRACT_VIOLATION"),
    ],
)
def test_failure_scenarios_are_quarantined_and_reconciled(
    tmp_path: Path, scenario: str, entity: str, code: str
) -> None:
    output = publish_structured_batch(_source(tmp_path, scenario), tmp_path / "output")
    quarantined = [
        json.loads(line)
        for line in (output / "quarantine/records.jsonl").read_text(encoding="utf-8").splitlines()
    ]

    assert any(row["entity"] == entity and row["code"] == code for row in quarantined)
    counts = _summary(output)["entities"][entity]
    assert counts["source_rows"] == counts["accepted_rows"] + counts["quarantined_rows"]


def test_unchanged_replay_is_a_byte_identical_no_op(tmp_path: Path) -> None:
    source = _source(tmp_path)
    first = publish_structured_batch(source, tmp_path / "output")
    before = {
        path.relative_to(first).as_posix(): path.read_bytes()
        for path in first.rglob("*")
        if path.is_file()
    }

    second = publish_structured_batch(source, tmp_path / "output")

    after = {
        path.relative_to(second).as_posix(): path.read_bytes()
        for path in second.rglob("*")
        if path.is_file()
    }
    assert first == second
    assert before == after


def test_existing_output_with_different_bytes_fails_closed(tmp_path: Path) -> None:
    source = _source(tmp_path)
    output = publish_structured_batch(source, tmp_path / "output")
    (output / "reconciliation.json").write_text("tampered\n", encoding="utf-8")

    with pytest.raises(ReplayConflict, match="different bytes"):
        publish_structured_batch(source, tmp_path / "output")
