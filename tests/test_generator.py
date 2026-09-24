from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from data.generate_enterprise_batch import SCENARIOS, generate_batch


def _generate(root: Path, *, scenario: str = "normal") -> Path:
    return generate_batch(
        root,
        business_date=date(2026, 9, 10),
        batch_id="enterprise-20260910",
        seed=23,
        accounts=4,
        scenario=scenario,
    )


def _relative_bytes(batch: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(batch)).replace("\\", "/"): path.read_bytes()
        for path in batch.rglob("*")
        if path.is_file()
    }


def test_same_inputs_generate_byte_identical_deliveries(tmp_path: Path) -> None:
    first = _generate(tmp_path / "first")
    second = _generate(tmp_path / "second")

    assert _relative_bytes(first) == _relative_bytes(second)
    assert set(_relative_bytes(first)) == {
        "_SUCCESS",
        "manifest.json",
        "structured/accounts.csv",
        "structured/invoices.csv",
        "structured/support_cases.csv",
        "structured/product_usage_daily.csv",
        "documents/documents.jsonl",
    }


def test_manifest_records_synthetic_scenario_and_complete_counts(tmp_path: Path) -> None:
    batch = _generate(tmp_path / "input", scenario="unauthorized-document")
    manifest = json.loads((batch / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["business_date"] == "2026-09-10"
    assert manifest["batch_id"] == "enterprise-20260910"
    assert manifest["schema_version"] == "1.0"
    assert manifest["scenario"] == "unauthorized-document"
    assert manifest["files"]["accounts"]["expected_row_count"] == 4
    assert manifest["files"]["invoices"]["expected_row_count"] == 8
    assert manifest["files"]["product_usage_daily"]["expected_row_count"] == 12
    assert manifest["files"]["documents"]["expected_row_count"] == 6


def test_existing_delivery_identity_is_never_overwritten(tmp_path: Path) -> None:
    _generate(tmp_path / "input")

    with pytest.raises(FileExistsError, match="immutable batch already exists"):
        _generate(tmp_path / "input")


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_every_failure_scenario_is_deterministic(tmp_path: Path, scenario: str) -> None:
    first = _generate(tmp_path / "first", scenario=scenario)
    second = _generate(tmp_path / "second", scenario=scenario)

    assert _relative_bytes(first) == _relative_bytes(second)
