from __future__ import annotations

from datetime import date
from pathlib import Path

import apache_beam as beam
from apache_beam.testing.test_pipeline import TestPipeline as BeamTestPipeline
from apache_beam.testing.util import assert_that, equal_to

from beam.document_pipeline import build_document_graph
from data.generate_enterprise_batch import generate_batch


def _lines(tmp_path: Path, scenario: str = "normal") -> list[str]:
    batch = generate_batch(
        tmp_path,
        business_date=date(2026, 9, 11),
        batch_id=f"documents-{scenario}",
        seed=71,
        accounts=3,
        scenario=scenario,
    )
    return (batch / "documents/documents.jsonl").read_text(encoding="utf-8").splitlines()


def test_complete_document_graph_validates_versions_chunks_and_reconciles(
    tmp_path: Path,
) -> None:
    with BeamTestPipeline(runner="DirectRunner") as pipeline:
        outputs = build_document_graph(
            pipeline,
            _lines(tmp_path),
            business_date=date(2026, 9, 11),
            batch_id="documents-normal",
        )
        document_ids = outputs.documents | "Normal document IDs" >> beam.Map(
            lambda document: document["document_id"]
        )
        assert_that(
            document_ids,
            equal_to(
                [
                    "DOC-CONTRACT-001",
                    "DOC-SLA-001",
                    "DOC-RUNBOOK-001",
                    "DOC-POLICY-001",
                    "DOC-INCIDENT-001",
                ]
            ),
            label="Assert normal documents",
        )
        assert_that(outputs.quarantined, equal_to([]), label="Assert no document quarantine")
        validation_counts = (
            outputs.validation_reconciliation
            | "Normal validation counts"
            >> beam.Map(
                lambda row: (
                    row["source_rows"],
                    row["contract_accepted_rows"],
                    row["contract_quarantined_rows"],
                    row["balanced"],
                )
            )
        )
        assert_that(
            validation_counts,
            equal_to([(5, 5, 0, True)]),
            label="Assert normal validation reconciliation",
        )
        chunk_ids = outputs.chunks | "Normal chunk IDs" >> beam.Map(lambda chunk: chunk["chunk_id"])
        assert_that(
            chunk_ids | "Count normal chunks" >> beam.combiners.Count.Globally(),
            equal_to([5]),
            label="Assert normal chunk count",
        )


def test_malformed_document_is_quarantined_without_breaking_reconciliation(
    tmp_path: Path,
) -> None:
    with BeamTestPipeline(runner="DirectRunner") as pipeline:
        outputs = build_document_graph(
            pipeline,
            _lines(tmp_path, "malformed-document"),
            business_date=date(2026, 9, 11),
            batch_id="documents-malformed-document",
        )
        codes = outputs.quarantined | "Malformed quarantine codes" >> beam.Map(
            lambda row: row["code"]
        )
        assert_that(codes, equal_to(["DOCUMENT_CONTRACT_VIOLATION"]))
        validation_counts = (
            outputs.validation_reconciliation
            | "Malformed validation counts"
            >> beam.Map(
                lambda row: (
                    row["source_rows"],
                    row["contract_accepted_rows"],
                    row["contract_quarantined_rows"],
                )
            )
        )
        assert_that(
            validation_counts,
            equal_to([(6, 5, 1)]),
            label="Assert malformed reconciliation",
        )


def test_version_update_marks_only_latest_effective_chunks_active(tmp_path: Path) -> None:
    with BeamTestPipeline(runner="DirectRunner") as pipeline:
        outputs = build_document_graph(
            pipeline,
            _lines(tmp_path, "document-version-update"),
            business_date=date(2026, 9, 11),
            batch_id="documents-document-version-update",
        )
        sla_versions = (
            outputs.chunks
            | "Keep SLA chunks" >> beam.Filter(lambda chunk: chunk["document_id"] == "DOC-SLA-001")
            | "Extract SLA version state"
            >> beam.Map(lambda chunk: (chunk["document_version"], chunk["is_active"]))
        )
        assert_that(sla_versions, equal_to([(1, False), (2, True)]))
        sla_metrics = (
            outputs.version_metrics
            | "Keep SLA version metrics"
            >> beam.Filter(lambda row: row["document_id"] == "DOC-SLA-001")
            | "Extract SLA outcome counts"
            >> beam.Map(
                lambda row: (
                    row["incoming_rows"],
                    row["accepted_new_rows"],
                    row["active_versions"],
                    row["historical_versions"],
                    row["balanced"],
                )
            )
        )
        assert_that(sla_metrics, equal_to([(2, 2, 1, 1, True)]))
