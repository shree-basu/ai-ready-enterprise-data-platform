"""Apache Beam document graph, executed locally with DirectRunner only."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import apache_beam as beam
from apache_beam import pvalue

from enterprise_platform.chunking import ChunkingConfig, chunk_document
from enterprise_platform.document_versions import resolve_document_versions
from enterprise_platform.documents import (
    document_quarantine_record,
    parse_and_validate_document,
)
from enterprise_platform.embedding_lineage import EmbeddingProcessingError, embed_chunks
from enterprise_platform.embeddings import (
    DeterministicLocalEmbeddingProvider,
    EmbeddingProvider,
)


@dataclass(frozen=True)
class BeamDocumentOutputs:
    documents: beam.PCollection
    chunks: beam.PCollection
    embedded_chunks: beam.PCollection
    embedding_failures: beam.PCollection
    quarantined: beam.PCollection
    validation_reconciliation: beam.PCollection
    embedding_reconciliation: beam.PCollection
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


class _EmbedChunk(beam.DoFn):
    def __init__(
        self,
        provider: EmbeddingProvider,
        embedded_at: datetime,
        batch_id: str,
    ) -> None:
        self.provider = provider
        self.embedded_at = embedded_at
        self.batch_id = batch_id

    def process(self, chunk: dict[str, Any]):
        try:
            yield embed_chunks(
                [chunk],
                self.provider,
                embedded_at=self.embedded_at,
            )[0]
        except EmbeddingProcessingError as exc:
            yield pvalue.TaggedOutput(
                "quarantine",
                {
                    "code": "EMBEDDING_FAILURE",
                    "message": str(exc),
                    "batch_id": self.batch_id,
                    "document_id": chunk["document_id"],
                    "chunk_id": chunk["chunk_id"],
                    "embedding_provider": self.provider.provider_name,
                    "embedding_model": self.provider.model_id,
                    "embedding_dimension": self.provider.dimension,
                    "embedding_version": self.provider.embedding_version,
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


def _reconcile_embeddings(item: tuple[str, dict[str, list[int]]]) -> dict[str, Any]:
    _, grouped = item
    chunks = sum(grouped["chunks"])
    embedded = sum(grouped["embedded"])
    quarantined = sum(grouped["quarantine"])
    if chunks != embedded + quarantined:
        raise RuntimeError("document embedding counts do not reconcile")
    return {
        "entity": "document_chunks",
        "source_chunks": chunks,
        "embedded_chunks": embedded,
        "embedding_quarantined_chunks": quarantined,
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
    embedding_provider: EmbeddingProvider | None = None,
    embedded_at: datetime | None = None,
) -> BeamDocumentOutputs:
    """Build validation, versioning, chunking, and embedding without cloud clients."""

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
    provider = embedding_provider or DeterministicLocalEmbeddingProvider()
    embedding_time = embedded_at or datetime.combine(business_date, datetime.min.time(), UTC)
    embedding_outputs = chunks | "Embed document chunks" >> beam.ParDo(
        _EmbedChunk(provider, embedding_time, batch_id)
    ).with_outputs("quarantine", main="embedded")
    embedding_failures = embedding_outputs.quarantine
    quarantined = (
        contract_quarantine,
        resolved.quarantine,
        embedding_failures,
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
    embedding_reconciliation = (
        {
            "chunks": _singleton_count(chunks, "chunks awaiting embedding"),
            "embedded": _singleton_count(embedding_outputs.embedded, "embedded chunks"),
            "quarantine": _singleton_count(embedding_failures, "embedding quarantine"),
        }
        | "Join embedding counts" >> beam.CoGroupByKey()
        | "Assert embedding reconciliation" >> beam.Map(_reconcile_embeddings)
    )
    return BeamDocumentOutputs(
        documents=resolved.documents,
        chunks=chunks,
        embedded_chunks=embedding_outputs.embedded,
        embedding_failures=embedding_failures,
        quarantined=quarantined,
        validation_reconciliation=validation_reconciliation,
        embedding_reconciliation=embedding_reconciliation,
        version_metrics=resolved.metrics,
    )
