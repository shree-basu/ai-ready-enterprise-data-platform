from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from pathlib import Path

import apache_beam as beam
from apache_beam.testing.test_pipeline import TestPipeline as BeamTestPipeline
from apache_beam.testing.util import assert_that, equal_to

from beam.document_pipeline import build_document_graph
from data.generate_enterprise_batch import generate_batch
from enterprise_platform.embeddings import EmbeddingProvider


def _lines(tmp_path: Path) -> list[str]:
    batch = generate_batch(
        tmp_path,
        business_date=date(2026, 9, 12),
        batch_id="documents-embedding",
        seed=79,
        accounts=3,
        scenario="normal",
    )
    return (batch / "documents/documents.jsonl").read_text(encoding="utf-8").splitlines()


class _FailingProvider(EmbeddingProvider):
    @property
    def provider_name(self) -> str:
        return "test-failure"

    @property
    def model_id(self) -> str:
        return "test-failure-v1"

    @property
    def dimension(self) -> int:
        return 8

    @property
    def embedding_version(self) -> str:
        return "test-failure-v1:dimension=8"

    def embed(self, texts: Sequence[str], *, task_type: str) -> list[list[float]]:
        raise RuntimeError("simulated offline provider failure")


def test_document_graph_embeds_chunks_locally_with_governance_lineage(
    tmp_path: Path,
) -> None:
    with BeamTestPipeline(runner="DirectRunner") as pipeline:
        outputs = build_document_graph(
            pipeline,
            _lines(tmp_path),
            business_date=date(2026, 9, 12),
            batch_id="documents-embedding",
        )
        lineage = outputs.embedded_chunks | "Extract embedded lineage" >> beam.Map(
            lambda row: (
                row["embedding_provider"],
                row["embedding_model"],
                row["embedding_dimension"],
                len(row["embedding"]),
                bool(row["classification"]),
                bool(row["allowed_groups"]),
            )
        )
        assert_that(
            lineage,
            equal_to(
                [
                    (
                        "deterministic-local",
                        "token-hash-embedding-v1",
                        64,
                        64,
                        True,
                        True,
                    ),
                ]
                * 5
            ),
            label="Assert local embedded lineage",
        )
        assert_that(outputs.embedding_failures, equal_to([]), label="Assert no embedding failures")
        reconciled = (
            outputs.embedding_reconciliation
            | "Extract successful embedding counts"
            >> beam.Map(
                lambda row: (
                    row["source_chunks"],
                    row["embedded_chunks"],
                    row["embedding_quarantined_chunks"],
                    row["balanced"],
                )
            )
        )
        assert_that(reconciled, equal_to([(5, 5, 0, True)]))


def test_embedding_provider_failure_is_quarantined_and_reconciled(tmp_path: Path) -> None:
    with BeamTestPipeline(runner="DirectRunner") as pipeline:
        outputs = build_document_graph(
            pipeline,
            _lines(tmp_path),
            business_date=date(2026, 9, 12),
            batch_id="documents-embedding-failure",
            embedding_provider=_FailingProvider(),
        )
        assert_that(outputs.embedded_chunks, equal_to([]), label="Assert no failed embeddings")
        failures = outputs.embedding_failures | "Extract embedding failures" >> beam.Map(
            lambda row: (row["code"], row["embedding_provider"], "chunk_text" in row)
        )
        assert_that(
            failures,
            equal_to([("EMBEDDING_FAILURE", "test-failure", False)] * 5),
            label="Assert embedding failure quarantine",
        )
        reconciled = (
            outputs.embedding_reconciliation
            | "Extract failed embedding counts"
            >> beam.Map(
                lambda row: (
                    row["source_chunks"],
                    row["embedded_chunks"],
                    row["embedding_quarantined_chunks"],
                    row["balanced"],
                )
            )
        )
        assert_that(reconciled, equal_to([(5, 0, 5, True)]))
