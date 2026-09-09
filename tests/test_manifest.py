from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from data.generate_enterprise_batch import generate_batch
from enterprise_platform.manifest import ManifestViolation, load_and_validate_manifest


def _batch(tmp_path: Path, *, scenario: str = "normal") -> Path:
    return generate_batch(
        tmp_path,
        business_date=date(2026, 9, 10),
        batch_id="delivery-001",
        seed=41,
        accounts=3,
        scenario=scenario,
    )


def _read_manifest(batch: Path) -> dict:
    return json.loads((batch / "manifest.json").read_text(encoding="utf-8"))


def _write_manifest(batch: Path, manifest: dict) -> None:
    (batch / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def test_valid_delivery_passes_exact_manifest_validation(tmp_path: Path) -> None:
    batch = _batch(tmp_path)

    manifest = load_and_validate_manifest(batch)

    assert manifest["batch_id"] == "delivery-001"
    assert set(manifest["files"]) == {
        "accounts",
        "invoices",
        "support_cases",
        "product_usage_daily",
        "documents",
    }


def test_changed_source_bytes_fail_checksum(tmp_path: Path) -> None:
    batch = _batch(tmp_path)
    with (batch / "structured/accounts.csv").open("a", encoding="utf-8") as handle:
        handle.write("tampered\n")

    with pytest.raises(ManifestViolation, match="row-count mismatch|checksum mismatch"):
        load_and_validate_manifest(batch)


def test_false_manifest_count_is_rejected(tmp_path: Path) -> None:
    batch = _batch(tmp_path)
    manifest = _read_manifest(batch)
    manifest["files"]["invoices"]["expected_row_count"] += 1
    _write_manifest(batch, manifest)

    with pytest.raises(ManifestViolation, match="row-count mismatch for invoices"):
        load_and_validate_manifest(batch)


def test_manifest_cannot_redirect_an_entity(tmp_path: Path) -> None:
    batch = _batch(tmp_path)
    manifest = _read_manifest(batch)
    manifest["files"]["accounts"]["path"] = "structured/invoices.csv"
    _write_manifest(batch, manifest)

    with pytest.raises(ManifestViolation, match="path redirect rejected for accounts"):
        load_and_validate_manifest(batch)


def test_missing_success_marker_is_rejected(tmp_path: Path) -> None:
    batch = _batch(tmp_path)
    (batch / "_SUCCESS").unlink()

    with pytest.raises(ManifestViolation, match="empty _SUCCESS"):
        load_and_validate_manifest(batch)


def test_wrong_csv_header_is_rejected_even_with_updated_checksum(tmp_path: Path) -> None:
    batch = _batch(tmp_path)
    accounts = batch / "structured/accounts.csv"
    contents = accounts.read_text(encoding="utf-8").replace("account_id", "customer_id", 1)
    accounts.write_text(contents, encoding="utf-8", newline="")
    manifest = _read_manifest(batch)
    import hashlib

    manifest["files"]["accounts"]["sha256"] = hashlib.sha256(accounts.read_bytes()).hexdigest()
    _write_manifest(batch, manifest)

    with pytest.raises(ManifestViolation, match="accounts CSV header"):
        load_and_validate_manifest(batch)


def test_unexpected_file_is_rejected(tmp_path: Path) -> None:
    batch = _batch(tmp_path)
    (batch / "extra.csv").write_text("unexpected\n", encoding="utf-8")

    with pytest.raises(ManifestViolation, match="missing or unexpected files"):
        load_and_validate_manifest(batch)


def test_directory_and_manifest_identity_must_match(tmp_path: Path) -> None:
    batch = _batch(tmp_path)
    manifest = _read_manifest(batch)
    manifest["batch_id"] = "redirected-batch"
    _write_manifest(batch, manifest)

    with pytest.raises(ManifestViolation, match="directory identity"):
        load_and_validate_manifest(batch)


def test_malformed_document_scenario_passes_transport_contract(tmp_path: Path) -> None:
    batch = _batch(tmp_path, scenario="malformed-document")

    manifest = load_and_validate_manifest(batch)

    assert manifest["scenario"] == "malformed-document"
