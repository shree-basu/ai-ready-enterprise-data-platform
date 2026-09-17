"""Governed local tools exposed to the enterprise data agent."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import duckdb

from enterprise_platform.audit import AuditEventType, LocalAuditLog, create_audit_event
from enterprise_platform.freshness import FreshnessThresholds, evaluate_freshness
from enterprise_platform.governance import GovernedRetriever, PrincipalContext
from enterprise_platform.operations import LocalOperationService
from enterprise_platform.sql_policy import (
    ReadOnlySQLPolicy,
    SQLPolicyViolation,
    execute_governed_query,
)


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_value(item) for item in value]
    return value


def _query_hash(query: str) -> str:
    return hashlib.sha256(query.encode()).hexdigest()


@dataclass(frozen=True)
class EnterpriseToolContext:
    principal: PrincipalContext
    analytics_connection: duckdb.DuckDBPyConnection
    sql_policy: ReadOnlySQLPolicy
    retriever: GovernedRetriever
    analytics_observed_at: datetime | None
    documents_observed_at: datetime | None
    latest_business_date: date | None
    latest_pipeline_run_id: str | None
    operation_service: LocalOperationService
    audit_log: LocalAuditLog
    clock: Callable[[], datetime]
    freshness_thresholds: FreshnessThresholds = FreshnessThresholds()
    max_sql_rows: int = 100

    def __post_init__(self) -> None:
        if self.max_sql_rows < 1:
            raise ValueError("max_sql_rows must be positive")


class EnterpriseAgentTools:
    """Bound tools; authorization context cannot be supplied or changed by the model."""

    def __init__(self, context: EnterpriseToolContext) -> None:
        self.context = context

    def _audit(
        self,
        event_type: AuditEventType,
        *,
        action: str,
        decision: str,
        object_ids: tuple[str, ...] = (),
        details: dict[str, Any] | None = None,
    ) -> None:
        self.context.audit_log.append(
            create_audit_event(
                event_type,
                occurred_at=self.context.clock(),
                actor_id=self.context.principal.user_id,
                action=action,
                decision=decision,
                object_ids=object_ids,
                details=details,
            )
        )

    def run_governed_sql(self, query: str) -> dict[str, Any]:
        """Run one bounded read-only SELECT against approved local serving views."""

        query_hash = _query_hash(query)
        try:
            validated = self.context.sql_policy.validate(query)
            result = execute_governed_query(
                self.context.analytics_connection,
                self.context.sql_policy,
                query,
                max_rows=self.context.max_sql_rows,
            )
        except SQLPolicyViolation:
            self._audit(
                AuditEventType.SQL_QUERY,
                action="run_governed_sql",
                decision="BLOCKED",
                details={"query_hash": query_hash, "error_code": "SQL_POLICY_VIOLATION"},
            )
            return {"status": "BLOCKED", "error_code": "SQL_POLICY_VIOLATION"}
        except duckdb.Error:
            self._audit(
                AuditEventType.SQL_QUERY,
                action="run_governed_sql",
                decision="FAILED",
                details={"query_hash": query_hash, "error_code": "QUERY_EXECUTION_ERROR"},
            )
            return {"status": "FAILED", "error_code": "QUERY_EXECUTION_ERROR"}

        self._audit(
            AuditEventType.SQL_QUERY,
            action="run_governed_sql",
            decision="ALLOWED",
            object_ids=tuple(sorted(validated.referenced_relations)),
            details={
                "query_hash": query_hash,
                "query_id": result.query_id,
                "rows_returned": len(result.rows),
                "truncated": result.truncated,
            },
        )
        return {
            "status": "OK",
            "query_id": result.query_id,
            "serving_objects": sorted(validated.referenced_relations),
            "columns": list(result.columns),
            "rows": _json_value(result.rows),
            "truncated": result.truncated,
            "data_freshness_timestamp": _json_value(self.context.analytics_observed_at),
        }

    def search_enterprise_knowledge(
        self, query: str, account_id: str = "", top_k: int = 5
    ) -> dict[str, Any]:
        """Search only documents permitted for the bound principal and optional account scope."""

        if not query.strip():
            raise ValueError("query is required")
        if not 1 <= top_k <= 20:
            raise ValueError("top_k must be between 1 and 20")
        result = self.context.retriever.search(
            query,
            principal=self.context.principal,
            account_id=account_id.strip() or None,
            k=top_k,
            candidate_k=max(20, top_k),
        )
        document_ids = tuple(evidence.document_id for evidence in result.evidence)
        self._audit(
            AuditEventType.RETRIEVAL,
            action="search_enterprise_knowledge",
            decision="ALLOWED" if result.evidence else "EMPTY",
            object_ids=document_ids,
            details={
                "result_count": len(result.evidence),
                "access_denied_candidates": result.access_denied_candidates,
                "account_scoped": bool(account_id.strip()),
                "retrieval_method": "hybrid",
            },
        )
        if result.access_denied_candidates:
            self._audit(
                AuditEventType.AUTHORIZATION,
                action="filter_retrieval_candidates",
                decision="DENIED_CANDIDATES_REMOVED",
                details={"denied_count": result.access_denied_candidates},
            )
        return {
            "status": "OK" if result.evidence else "EMPTY",
            "empty_reason": result.empty_reason,
            "evidence": [_json_value(asdict(evidence)) for evidence in result.evidence],
            "documents_freshness_timestamp": _json_value(self.context.documents_observed_at),
        }

    def get_data_freshness(self) -> dict[str, Any]:
        """Return analytics/document freshness and expose every stale-source discrepancy."""

        report = evaluate_freshness(
            evaluated_at=self.context.clock(),
            analytics_observed_at=self.context.analytics_observed_at,
            documents_observed_at=self.context.documents_observed_at,
            thresholds=self.context.freshness_thresholds,
        )
        self._audit(
            AuditEventType.AGENT_TOOL_CALL,
            action="get_data_freshness",
            decision=report.state.value,
            object_ids=tuple(
                value for value in (self.context.latest_pipeline_run_id,) if value is not None
            ),
            details={"discrepancy_count": len(report.discrepancies)},
        )
        return {
            "status": report.state.value,
            "safe_for_answer": report.safe_for_answer,
            "latest_business_date": _json_value(self.context.latest_business_date),
            "latest_successful_pipeline_run": self.context.latest_pipeline_run_id,
            "analytics": _json_value(asdict(report.analytics)),
            "documents": _json_value(asdict(report.documents)),
            "discrepancies": list(report.discrepancies),
        }

    def request_pipeline_reprocessing(self, target_run_id: str, reason: str) -> dict[str, Any]:
        """Create a PENDING_APPROVAL local request; never approve or execute it."""

        request = self.context.operation_service.request_reprocessing(
            target_run_id=target_run_id,
            requested_by=self.context.principal.user_id,
            reason=reason,
            created_at=self.context.clock(),
        )
        self._audit(
            AuditEventType.AGENT_TOOL_CALL,
            action="request_pipeline_reprocessing",
            decision=request.status.value,
            object_ids=(request.request_id, request.target_run_id),
            details={"operation_type": request.operation_type.value},
        )
        return {
            "request_id": request.request_id,
            "operation_type": request.operation_type.value,
            "target_run_id": request.target_run_id,
            "status": request.status.value,
            "created_at": request.created_at.isoformat(),
        }
