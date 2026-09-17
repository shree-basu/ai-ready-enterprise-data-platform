"""Human-approved local operation requests with no cloud execution path."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum

from enterprise_platform.audit import (
    AuditEventType,
    LocalAuditLog,
    create_audit_event,
)


class OperationType(StrEnum):
    REPROCESS_PIPELINE = "REPROCESS_PIPELINE"


class OperationStatus(StrEnum):
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    EXECUTED_LOCALLY = "EXECUTED_LOCALLY"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True)
class OperationRequest:
    request_id: str
    operation_type: OperationType
    target_run_id: str
    requested_by: str
    reason: str
    status: OperationStatus
    created_at: datetime
    approved_by: str | None = None
    approved_at: datetime | None = None
    completed_by: str | None = None
    completed_at: datetime | None = None


def _aware_utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone")
    return value.astimezone(UTC)


def _required(value: str, field: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field} is required")
    return normalized


def _request_id(
    operation_type: OperationType,
    target_run_id: str,
    requested_by: str,
    reason: str,
    created_at: datetime,
) -> str:
    identity = "\x1f".join(
        (
            operation_type.value,
            target_run_id,
            requested_by,
            reason,
            created_at.isoformat(),
        )
    )
    return hashlib.sha256(identity.encode()).hexdigest()


class LocalOperationService:
    """Persist state transitions in memory and permit only a local simulation receipt."""

    def __init__(self, audit_log: LocalAuditLog) -> None:
        self.audit_log = audit_log
        self._requests: dict[str, OperationRequest] = {}

    def _audit(
        self,
        request: OperationRequest,
        *,
        actor_id: str,
        occurred_at: datetime,
        action: str,
    ) -> None:
        self.audit_log.append(
            create_audit_event(
                AuditEventType.OPERATION_REQUEST,
                occurred_at=occurred_at,
                actor_id=actor_id,
                action=action,
                decision=request.status.value,
                object_ids=(request.request_id, request.target_run_id),
                details={"operation_type": request.operation_type.value},
            )
        )

    def request_reprocessing(
        self,
        *,
        target_run_id: str,
        requested_by: str,
        reason: str,
        created_at: datetime,
    ) -> OperationRequest:
        target = _required(target_run_id, "target_run_id")
        requester = _required(requested_by, "requested_by")
        normalized_reason = _required(reason, "reason")
        created = _aware_utc(created_at, "created_at")
        request = OperationRequest(
            request_id=_request_id(
                OperationType.REPROCESS_PIPELINE,
                target,
                requester,
                normalized_reason,
                created,
            ),
            operation_type=OperationType.REPROCESS_PIPELINE,
            target_run_id=target,
            requested_by=requester,
            reason=normalized_reason,
            status=OperationStatus.PENDING_APPROVAL,
            created_at=created,
        )
        existing = self._requests.get(request.request_id)
        if existing is not None:
            return existing
        self._requests[request.request_id] = request
        self._audit(request, actor_id=requester, occurred_at=created, action="REQUESTED")
        return request

    def approve(
        self, request_id: str, *, approved_by: str, approved_at: datetime
    ) -> OperationRequest:
        request = self.get(request_id)
        approver = _required(approved_by, "approved_by")
        occurred = _aware_utc(approved_at, "approved_at")
        if request.status is not OperationStatus.PENDING_APPROVAL:
            raise ValueError("only pending operations can be approved")
        if approver == request.requested_by:
            raise ValueError("requester cannot approve their own operation")
        if occurred < request.created_at:
            raise ValueError("approval cannot precede request creation")
        approved = replace(
            request,
            status=OperationStatus.APPROVED,
            approved_by=approver,
            approved_at=occurred,
        )
        self._requests[request_id] = approved
        self._audit(approved, actor_id=approver, occurred_at=occurred, action="APPROVED")
        return approved

    def execute_local(
        self, request_id: str, *, executed_by: str, executed_at: datetime
    ) -> OperationRequest:
        request = self.get(request_id)
        executor = _required(executed_by, "executed_by")
        occurred = _aware_utc(executed_at, "executed_at")
        if request.status is not OperationStatus.APPROVED:
            raise ValueError("local execution requires prior approval")
        assert request.approved_at is not None
        if occurred < request.approved_at:
            raise ValueError("execution cannot precede approval")
        completed = replace(
            request,
            status=OperationStatus.EXECUTED_LOCALLY,
            completed_by=executor,
            completed_at=occurred,
        )
        self._requests[request_id] = completed
        self._audit(
            completed,
            actor_id=executor,
            occurred_at=occurred,
            action="LOCAL_SIMULATION_COMPLETED",
        )
        return completed

    def cancel(
        self, request_id: str, *, cancelled_by: str, cancelled_at: datetime
    ) -> OperationRequest:
        request = self.get(request_id)
        actor = _required(cancelled_by, "cancelled_by")
        occurred = _aware_utc(cancelled_at, "cancelled_at")
        if request.status not in {
            OperationStatus.PENDING_APPROVAL,
            OperationStatus.APPROVED,
        }:
            raise ValueError("only pending or approved operations can be cancelled")
        if occurred < request.created_at:
            raise ValueError("cancellation cannot precede request creation")
        cancelled = replace(
            request,
            status=OperationStatus.CANCELLED,
            completed_by=actor,
            completed_at=occurred,
        )
        self._requests[request_id] = cancelled
        self._audit(cancelled, actor_id=actor, occurred_at=occurred, action="CANCELLED")
        return cancelled

    def get(self, request_id: str) -> OperationRequest:
        try:
            return self._requests[request_id]
        except KeyError as exc:
            raise KeyError(f"unknown operation request: {request_id}") from exc

    @property
    def requests(self) -> tuple[OperationRequest, ...]:
        return tuple(
            sorted(self._requests.values(), key=lambda item: (item.created_at, item.request_id))
        )
