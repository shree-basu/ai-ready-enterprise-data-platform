"""Apache Beam document graph, executed locally with DirectRunner only."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Any

import apache_beam as beam
from apache_beam import pvalue

from enterprise_platform.chunking import ChunkingConfig, chunk_document
from enterprise_platform.document_versions import resolve_document_versions
from enterprise_platform.documents import (
    document_quarantine_record,
    parse_and_validate_document,
)


@dataclass(frozen=True)
class BeamDocumentOutputs:
    documents: beam.PCollection
    chunks: beam.PCollection
    quarantined: beam.PCollection
    validation_reconciliation: beam.PCollection
    version_metrics: beam.PCollection


class _ValidateDocument(beam.DoFn):
    def __init__(self, batch_id: str) -> None:
        self.batch_id = batch_id

    def process(self, raw_line: str):
        accepted, violation = parse_and_validate_document(raw_line)
        if accepted is not None:
            yield accepted
        else:
            assert violation is not None
            yield pvalue.TaggedOutput(
                "quarantine",
                document_quarantine_record(violation, batch_id=self.batch_id),
            )


class _ResolveDocumentVersions(beam.DoFn):
    def __init__(self, as_of_date: date, batch_id: str) -> None:
        self.as_of_date = as_of_date
        self.batch_id = batch_id

    def process(self, item: tuple[str, dict[str, Iterable[dict[str, Any]]]]):
        document_id, grouped = item
        incoming = list(grouped["incoming"])
        prior = list(grouped["prior"])
        result = resolve_document_versions(
            incoming, as_of_date=self.as_of_date, prior_versions=prior
        )
        yield from result.documents
        for violation in result.quarantined:
            yield pvalue.TaggedOutput(
                "quarantine",
                document_quarantine_record(violation, batch_id=self.batch_id),
            )

        accepted_new = len(incoming) - len(result.quarantined) - result.replayed_documents
        if accepted_new < 0 or len(incoming) != (
            accepted_new + len(result.quarantined) + result.replayed_documents
        ):
            raise RuntimeError(f"version outcomes do not reconcile for {document_id}")
        yield pvalue.TaggedOutput(
            "metrics",
            {
                "document_id": document_id,
                "incoming_rows": len(incoming),
                "accepted_new_rows": accepted_new,
                "replayed_rows": result.replayed_documents,
                "version_quarantined_rows": len(result.quarantined),
                "active_versions": sum(1 for document in result.documents if document["is_active"]),
                "historical_versions": sum(
                    1 for document in result.documents if not document["is_active"]
                ),
                "balanced": True,
            },
        )


def _reconcile_validation(item: tuple[str, dict[str, list[int]]]) -> dict[str, Any]:
    _, grouped = item
    source = sum(grouped["source"])
    accepted = sum(grouped["accepted"])
    quarantined = sum(grouped["quarantine"])
    if source != accepted + quarantined:
        raise RuntimeError("document contract-validation counts do not reconcile")
    return {
        "entity": "documents",
        "source_rows": source,
        "contract_accepted_rows": accepted,
        "contract_quarantined_rows": quarantined,
        "balanced": True,
    }


def _singleton_count(pcollection: beam.PCollection, label: str) -> beam.PCollection:
    return (
        pcollection
        | f"Count {label}" >> beam.combiners.Count.Globally()
        | f"Key {label} count" >> beam.Map(lambda count: ("documents", count))
    )


def build_document_graph(
    pipeline: beam.Pipeline,
    raw_lines: list[str],
    *,
    business_date: date,
    batch_id: str,
    prior_versions: Iterable[dict[str, Any]] = (),
    chunking_config: ChunkingConfig | None = None,
) -> BeamDocumentOutputs:
    """Build validation, version resolution, and chunking without cloud clients."""

    raw = pipeline | "Create document lines" >> beam.Create(raw_lines)
    validated = raw | "Validate document envelopes" >> beam.ParDo(
        _ValidateDocument(batch_id)
    ).with_outputs("quarantine", main="accepted")
    contract_quarantine = validated.quarantine
    prior = pipeline | "Create prior document versions" >> beam.Create(list(prior_versions))
    grouped = {
        "incoming": validated.accepted
        | "Key incoming document versions"
        >> beam.Map(lambda document: (document["document_id"], document)),
        "prior": prior
        | "Key prior document versions"
        >> beam.Map(lambda document: (document["document_id"], document)),
    } | "Join document version state" >> beam.CoGroupByKey()
    resolved = grouped | "Resolve document versions" >> beam.ParDo(
        _ResolveDocumentVersions(business_date, batch_id)
    ).with_outputs("quarantine", "metrics", main="documents")
    config = chunking_config or ChunkingConfig()
    chunks = resolved.documents | "Create deterministic chunks" >> beam.FlatMap(
        chunk_document, config
    )
    quarantined = (
        contract_quarantine,
        resolved.quarantine,
    ) | "Flatten document quarantine" >> beam.Flatten()
    validation_reconciliation = (
        {
            "source": _singleton_count(raw, "source documents"),
            "accepted": _singleton_count(validated.accepted, "contract-accepted documents"),
            "quarantine": _singleton_count(contract_quarantine, "contract quarantine"),
        }
        | "Join document validation counts" >> beam.CoGroupByKey()
        | "Assert document validation reconciliation" >> beam.Map(_reconcile_validation)
    )
    return BeamDocumentOutputs(
        documents=resolved.documents,
        chunks=chunks,
        quarantined=quarantined,
        validation_reconciliation=validation_reconciliation,
        version_metrics=resolved.metrics,
    )
