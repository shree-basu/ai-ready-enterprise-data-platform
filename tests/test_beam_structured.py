from __future__ import annotations

from datetime import date
from pathlib import Path

import apache_beam as beam
from apache_beam.testing.test_pipeline import TestPipeline as BeamTestPipeline
from apache_beam.testing.util import assert_that, equal_to

from beam.structured_pipeline import build_structured_graph
from data.generate_enterprise_batch import generate_batch
from enterprise_platform.structured import read_structured_batch


def _raw(tmp_path: Path, scenario: str = "normal") -> dict[str, list[dict]]:
    batch = generate_batch(
        tmp_path,
        business_date=date(2026, 9, 10),
        batch_id=f"beam-{scenario}",
        seed=61,
        accounts=3,
        scenario=scenario,
    )
    _, raw = read_structured_batch(batch)
    return raw


def test_complete_directrunner_graph_accepts_and_reconciles_normal_batch(
    tmp_path: Path,
) -> None:
    with BeamTestPipeline(runner="DirectRunner") as pipeline:
        outputs = build_structured_graph(
            pipeline,
            _raw(tmp_path),
            business_date=date(2026, 9, 10),
            batch_id="beam-normal",
        )
        account_ids = outputs.accepted["accounts"] | "Extract account IDs" >> beam.Map(
            lambda row: row["account_id"]
        )
        assert_that(account_ids, equal_to(["A-1001", "A-1002", "A-1003"]))
        assert_that(outputs.quarantined, equal_to([]), label="Assert no quarantine")
        balanced = outputs.reconciliation | "Extract balanced flags" >> beam.Map(
            lambda row: row["balanced"]
        )
        assert_that(balanced, equal_to([True, True, True, True]), label="Assert balanced")


def test_directrunner_graph_quarantines_orphan_and_preserves_reconciliation(
    tmp_path: Path,
) -> None:
    with BeamTestPipeline(runner="DirectRunner") as pipeline:
        outputs = build_structured_graph(
            pipeline,
            _raw(tmp_path, "orphan-reference"),
            business_date=date(2026, 9, 10),
            batch_id="beam-orphan-reference",
        )
        codes = outputs.quarantined | "Extract quarantine codes" >> beam.Map(
            lambda row: (row["entity"], row["code"])
        )
        assert_that(codes, equal_to([("invoices", "ORPHAN_ACCOUNT_REFERENCE")]))
        invoice_reconciliation = (
            outputs.reconciliation
            | "Keep invoice reconciliation" >> beam.Filter(lambda row: row["entity"] == "invoices")
            | "Select invoice counts"
            >> beam.Map(
                lambda row: (
                    row["source_rows"],
                    row["accepted_rows"],
                    row["quarantined_rows"],
                )
            )
        )
        assert_that(invoice_reconciliation, equal_to([(6, 5, 1)]), label="Assert invoice counts")
