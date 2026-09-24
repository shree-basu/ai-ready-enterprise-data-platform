"""Run the complete deterministic enterprise-platform path without network access."""

from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import apache_beam as beam
from apache_beam.testing.util import assert_that, equal_to

from agent.agent import build_enterprise_agent, run_local_turn
from agent.tools import EnterpriseAgentTools, EnterpriseToolContext
from beam.document_pipeline import build_document_graph
from beam.structured_pipeline import build_structured_graph
from data.generate_enterprise_batch import generate_batch
from enterprise_platform.analytics_store import LocalAnalyticsStore
from enterprise_platform.audit import LocalAuditLog
from enterprise_platform.chunking import chunk_document
from enterprise_platform.document_versions import resolve_document_versions
from enterprise_platform.documents import parse_and_validate_document
from enterprise_platform.embedding_lineage import embed_chunks
from enterprise_platform.embeddings import DeterministicLocalEmbeddingProvider
from enterprise_platform.governance import GovernedRetriever, PrincipalContext
from enterprise_platform.lexical_index import LocalLexicalIndex
from enterprise_platform.observability import LocalMetricsRegistry, MetricName
from enterprise_platform.operations import LocalOperationService
from enterprise_platform.retrieval import LocalRetriever
from enterprise_platform.sql_policy import ReadOnlySQLPolicy
from enterprise_platform.structured import process_structured_records, read_structured_batch
from enterprise_platform.vector_store import LocalVectorIndex


@dataclass(frozen=True)
class DemoReport:
    scenario: str
    business_date: str
    batch_id: str
    pipeline_run_id: str
    structured_accepted_rows: int
    structured_quarantined_rows: int
    documents_processed: int
    chunks_indexed: int
    publication_inserted_rows: int
    agent_tool_calls: tuple[str, ...]
    agent_answers: tuple[str, ...]
    operation_status: str
    audit_events: int
    metrics: tuple[dict[str, Any], ...]
    cloud_operations: tuple[str, ...]


def _run_beam_graphs(
    raw_by_entity: dict[str, list[dict[str, Any]]],
    document_lines: list[str],
    *,
    business_date: date,
    batch_id: str,
) -> None:
    with beam.Pipeline(runner="DirectRunner") as pipeline:
        structured = build_structured_graph(
            pipeline,
            raw_by_entity,
            business_date=business_date,
            batch_id=batch_id,
        )
        balanced = structured.reconciliation | "Demo structured balance" >> beam.Map(
            lambda row: row["balanced"]
        )
        assert_that(balanced, equal_to([True] * 4), label="Verify demo structured balance")
        assert_that(
            structured.quarantined,
            equal_to([]),
            label="Verify demo structured quarantine empty",
        )

    with beam.Pipeline(runner="DirectRunner") as pipeline:
        documents = build_document_graph(
            pipeline,
            document_lines,
            business_date=business_date,
            batch_id=batch_id,
        )
        assert_that(
            documents.quarantined,
            equal_to([]),
            label="Verify demo document quarantine empty",
        )
        embedding_balance = (
            documents.embedding_reconciliation
            | "Demo embedding balance" >> beam.Map(lambda row: row["balanced"])
        )
        assert_that(embedding_balance, equal_to([True]), label="Verify demo embedding balance")


def _documents(document_lines: list[str], *, business_date: date) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for line in document_lines:
        document, violation = parse_and_validate_document(line)
        if violation is not None or document is None:
            raise RuntimeError("normal demo document failed its contract")
        normalized.append(document)
    resolved = resolve_document_versions(normalized, as_of_date=business_date)
    if resolved.quarantined:
        raise RuntimeError("normal demo document version failed closed")
    return resolved.documents


async def _agent_turns(tools: EnterpriseAgentTools) -> tuple[tuple[str, ...], tuple[str, ...]]:
    agent = build_enterprise_agent(tools)
    questions = (
        "Show overdue invoice balance for A-1001",
        "Which approved API failure triage runbook applies to A-1001?",
        "Are analytics and documents fresh?",
        "Please reprocess run-demo-001",
    )
    results = []
    for index, question in enumerate(questions, start=1):
        results.append(
            await run_local_turn(
                agent,
                question,
                user_id="demo-support-user",
                session_id=f"demo-session-{index}",
            )
        )
    return (
        tuple(call for result in results for call in result.tool_calls),
        tuple(result.text for result in results),
    )


def run_demo(workdir: Path) -> DemoReport:
    """Execute every local platform layer and return a machine-checkable summary."""

    business_date = date(2026, 9, 18)
    batch_id = "demo-001"
    batch = generate_batch(
        workdir / "input",
        business_date=business_date,
        batch_id=batch_id,
        seed=91,
        accounts=3,
        scenario="normal",
    )
    manifest, raw_by_entity = read_structured_batch(batch)
    document_lines = (batch / "documents/documents.jsonl").read_text(encoding="utf-8").splitlines()
    _run_beam_graphs(
        raw_by_entity,
        document_lines,
        business_date=business_date,
        batch_id=batch_id,
    )

    structured = process_structured_records(
        raw_by_entity,
        business_date=business_date,
        batch_id=batch_id,
    )
    documents = _documents(document_lines, business_date=business_date)
    chunks = [chunk for document in documents for chunk in chunk_document(document)]
    embedded_at = datetime.combine(business_date, datetime.min.time(), UTC)
    provider = DeterministicLocalEmbeddingProvider()
    embedded = embed_chunks(chunks, provider, embedded_at=embedded_at)
    retriever = GovernedRetriever(
        LocalRetriever(LocalVectorIndex(embedded), LocalLexicalIndex(embedded)),
        provider,
    )

    audit = LocalAuditLog()
    metrics = LocalMetricsRegistry()
    for entity, reconciliation in structured.reconciliation.items():
        dimensions = {"entity": entity}
        metrics.increment(MetricName.INPUT_ROWS, reconciliation.source_rows, dimensions=dimensions)
        metrics.increment(
            MetricName.ACCEPTED_ROWS, reconciliation.accepted_rows, dimensions=dimensions
        )
        metrics.increment(
            MetricName.QUARANTINED_ROWS,
            reconciliation.quarantined_rows,
            dimensions=dimensions,
        )
    metrics.increment(MetricName.DOCUMENTS_PROCESSED, len(documents))
    metrics.increment(MetricName.CHUNKS_GENERATED, len(chunks))
    metrics.increment(MetricName.EMBEDDINGS_GENERATED, len(embedded))

    now = datetime.combine(business_date + timedelta(days=1), datetime.min.time(), UTC)
    with LocalAnalyticsStore() as store:
        publication = store.publish(structured)
        operations = LocalOperationService(audit)
        tools = EnterpriseAgentTools(
            EnterpriseToolContext(
                principal=PrincipalContext("demo-support-user", frozenset({"support"}), "AMER"),
                analytics_connection=store.connection,
                sql_policy=ReadOnlySQLPolicy({"serving_account_health"}),
                retriever=retriever,
                analytics_observed_at=embedded_at,
                documents_observed_at=max(document["updated_at"] for document in documents),
                latest_business_date=business_date,
                latest_pipeline_run_id=structured.pipeline_run_id,
                operation_service=operations,
                audit_log=audit,
                clock=lambda: now,
                metrics=metrics,
            )
        )
        tool_calls, answers = asyncio.run(_agent_turns(tools))
        operation_status = operations.requests[0].status.value

    metric_records = tuple(
        {
            "name": point.name.value,
            "value": point.value,
            "unit": point.unit,
            "dimensions": dict(point.dimensions),
        }
        for point in metrics.snapshot
    )
    return DemoReport(
        scenario=str(manifest["scenario"]),
        business_date=business_date.isoformat(),
        batch_id=batch_id,
        pipeline_run_id=structured.pipeline_run_id,
        structured_accepted_rows=sum(len(rows) for rows in structured.accepted.values()),
        structured_quarantined_rows=len(structured.quarantined),
        documents_processed=len(documents),
        chunks_indexed=len(embedded),
        publication_inserted_rows=publication.inserted_rows,
        agent_tool_calls=tool_calls,
        agent_answers=answers,
        operation_status=operation_status,
        audit_events=len(audit.events),
        metrics=metric_records,
        cloud_operations=(),
    )


def _print(report: DemoReport) -> None:
    print(json.dumps(asdict(report), sort_keys=True, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=("account-risk",), default="account-risk")
    parser.add_argument("--workdir", type=Path)
    args = parser.parse_args()
    if args.workdir is not None:
        _print(run_demo(args.workdir))
        return
    with tempfile.TemporaryDirectory(prefix="enterprise-platform-demo-") as directory:
        _print(run_demo(Path(directory)))


if __name__ == "__main__":
    main()
