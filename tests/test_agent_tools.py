from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import duckdb

from agent.tools import EnterpriseAgentTools, EnterpriseToolContext
from enterprise_platform.audit import AuditEventType, LocalAuditLog
from enterprise_platform.embeddings import DeterministicLocalEmbeddingProvider
from enterprise_platform.governance import GovernedRetriever, PrincipalContext
from enterprise_platform.lexical_index import LocalLexicalIndex
from enterprise_platform.observability import LocalMetricsRegistry, MetricName
from enterprise_platform.operations import LocalOperationService, OperationStatus
from enterprise_platform.retrieval import LocalRetriever
from enterprise_platform.sql_policy import ReadOnlySQLPolicy
from enterprise_platform.vector_store import LocalVectorIndex


def _row(
    provider: DeterministicLocalEmbeddingProvider,
    chunk_id: str,
    text: str,
    *,
    groups: list[str],
    account_id: str | None,
) -> dict[str, object]:
    return {
        "chunk_id": chunk_id,
        "document_id": f"DOC-{chunk_id.upper()}",
        "document_version": 1,
        "title": f"Title {chunk_id}",
        "chunk_text": text,
        "content_hash": f"hash-{chunk_id}",
        "source_uri": f"synthetic://knowledge/{chunk_id}",
        "classification": "RESTRICTED",
        "allowed_groups": groups,
        "account_id": account_id,
        "embedding_id": f"embedding-{chunk_id}",
        "embedding": provider.embed([text], task_type="RETRIEVAL_DOCUMENT")[0],
        "embedding_provider": provider.provider_name,
        "embedding_model": provider.model_id,
        "embedding_dimension": provider.dimension,
        "embedding_version": provider.embedding_version,
        "is_active": True,
    }


def _tools(*, stale_analytics: bool = False):
    connection = duckdb.connect(":memory:")
    connection.execute(
        """
        CREATE VIEW serving_account_health AS
        SELECT 'A-1001' AS account_id, 1 AS overdue_invoices, 2500 AS overdue_amount
        """
    )
    provider = DeterministicLocalEmbeddingProvider(dimension=16)
    rows = [
        _row(
            provider,
            "secret",
            "API incident response runbook with restricted legal instructions",
            groups=["legal"],
            account_id="A-1001",
        ),
        _row(
            provider,
            "allowed",
            "API incident response runbook for support",
            groups=["support"],
            account_id="A-1001",
        ),
        _row(
            provider,
            "other-account",
            "API incident response for another account",
            groups=["support"],
            account_id="A-2002",
        ),
    ]
    retriever = GovernedRetriever(
        LocalRetriever(LocalVectorIndex(rows), LocalLexicalIndex(rows)), provider
    )
    audit = LocalAuditLog()
    operations = LocalOperationService(audit)
    metrics = LocalMetricsRegistry()
    now = datetime(2026, 9, 17, 10, tzinfo=UTC)
    context = EnterpriseToolContext(
        principal=PrincipalContext("support-1", frozenset({"support"}), "AMER"),
        analytics_connection=connection,
        sql_policy=ReadOnlySQLPolicy({"serving_account_health"}),
        retriever=retriever,
        analytics_observed_at=now - timedelta(days=4 if stale_analytics else 1),
        documents_observed_at=now - timedelta(hours=1),
        latest_business_date=date(2026, 9, 16),
        latest_pipeline_run_id="run-20260916",
        operation_service=operations,
        audit_log=audit,
        clock=lambda: now,
        max_sql_rows=10,
        metrics=metrics,
    )
    return EnterpriseAgentTools(context), connection, audit, operations


def test_governed_sql_returns_provenance_and_blocks_unsafe_sql() -> None:
    tools, connection, audit, _ = _tools()
    try:
        allowed = tools.run_governed_sql(
            "SELECT account_id, overdue_amount FROM serving_account_health"
        )
        blocked = tools.run_governed_sql("DROP VIEW serving_account_health")
    finally:
        connection.close()

    assert allowed["status"] == "OK"
    assert allowed["serving_objects"] == ["serving_account_health"]
    assert allowed["query_id"]
    assert allowed["rows"] == [["A-1001", 2500]]
    assert blocked == {"status": "BLOCKED", "error_code": "SQL_POLICY_VIOLATION"}
    assert [event.decision for event in audit.by_type(AuditEventType.SQL_QUERY)] == [
        "ALLOWED",
        "BLOCKED",
    ]
    assert all("sql" not in event.details for event in audit.events)


def test_knowledge_search_filters_acl_and_account_before_returning_content() -> None:
    tools, connection, audit, _ = _tools()
    try:
        result = tools.search_enterprise_knowledge(
            "API incident response runbook", account_id="A-1001", top_k=5
        )
    finally:
        connection.close()

    assert [item["chunk_id"] for item in result["evidence"]] == ["allowed"]
    assert result["evidence"][0]["content_kind"] == "UNTRUSTED_DOCUMENT_DATA"
    assert result["evidence"][0]["citation_id"] == "DOC-ALLOWED#allowed"
    assert audit.by_type(AuditEventType.AUTHORIZATION)[0].details["denied_count"] == 1


def test_freshness_tool_surfaces_mixed_source_state() -> None:
    tools, connection, _, _ = _tools(stale_analytics=True)
    try:
        result = tools.get_data_freshness()
    finally:
        connection.close()

    assert result["status"] == "STALE"
    assert result["safe_for_answer"] is False
    assert "analytics freshness is STALE" in result["discrepancies"]
    assert result["latest_successful_pipeline_run"] == "run-20260916"


def test_agent_can_only_create_pending_operation_request() -> None:
    tools, connection, audit, operations = _tools()
    try:
        result = tools.request_pipeline_reprocessing("run-20260916", "Reconcile corrected manifest")
    finally:
        connection.close()

    assert result["status"] == "PENDING_APPROVAL"
    assert operations.get(result["request_id"]).status is OperationStatus.PENDING_APPROVAL
    assert not hasattr(tools, "approve_operation")
    assert len(audit.by_type(AuditEventType.OPERATION_REQUEST)) == 1
    assert len(audit.by_type(AuditEventType.AGENT_TOOL_CALL)) == 1


def test_governed_tools_emit_bounded_local_metrics() -> None:
    tools, connection, _, _ = _tools()
    metrics = tools.context.metrics
    assert metrics is not None
    try:
        tools.run_governed_sql("SELECT account_id FROM serving_account_health")
        tools.run_governed_sql("DELETE FROM serving_account_health")
        tools.search_enterprise_knowledge("API incident response", account_id="A-1001")
    finally:
        connection.close()

    assert metrics.value(MetricName.SQL_QUERIES) == 2
    assert metrics.value(MetricName.BLOCKED_SQL_ATTEMPTS) == 1
    dimensions = {"retrieval_method": "hybrid"}
    assert metrics.value(MetricName.RETRIEVAL_REQUESTS, dimensions=dimensions) == 1
    assert metrics.value(MetricName.ACCESS_DENIED_CANDIDATES, dimensions=dimensions) == 1
