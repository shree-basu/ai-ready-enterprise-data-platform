from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from enterprise_platform.audit import AuditEventType, LocalAuditLog
from enterprise_platform.operations import LocalOperationService, OperationStatus


def _service():
    audit = LocalAuditLog()
    return LocalOperationService(audit), audit


def _request(service: LocalOperationService):
    return service.request_reprocessing(
        target_run_id="run-20260917",
        requested_by="analyst-1",
        reason="Reconcile corrected source manifest",
        created_at=datetime(2026, 9, 17, 8, tzinfo=UTC),
    )


def test_request_is_pending_deterministic_and_replay_safe() -> None:
    service, audit = _service()
    first = _request(service)
    replay = _request(service)

    assert first == replay
    assert first.status is OperationStatus.PENDING_APPROVAL
    assert len(service.requests) == 1
    assert len(audit.by_type(AuditEventType.OPERATION_REQUEST)) == 1


def test_approval_requires_separation_of_duties() -> None:
    service, _ = _service()
    request = _request(service)

    with pytest.raises(ValueError, match="cannot approve"):
        service.approve(
            request.request_id,
            approved_by="analyst-1",
            approved_at=request.created_at + timedelta(minutes=1),
        )


def test_local_execution_requires_approval_and_records_every_transition() -> None:
    service, audit = _service()
    request = _request(service)
    with pytest.raises(ValueError, match="requires prior approval"):
        service.execute_local(
            request.request_id,
            executed_by="local-operator",
            executed_at=request.created_at + timedelta(minutes=2),
        )

    approved = service.approve(
        request.request_id,
        approved_by="approver-1",
        approved_at=request.created_at + timedelta(minutes=1),
    )
    executed = service.execute_local(
        request.request_id,
        executed_by="local-operator",
        executed_at=request.created_at + timedelta(minutes=2),
    )

    assert approved.status is OperationStatus.APPROVED
    assert executed.status is OperationStatus.EXECUTED_LOCALLY
    assert executed.completed_by == "local-operator"
    events = audit.by_type(AuditEventType.OPERATION_REQUEST)
    assert [event.action for event in events] == [
        "REQUESTED",
        "APPROVED",
        "LOCAL_SIMULATION_COMPLETED",
    ]


def test_cancelled_and_completed_requests_are_terminal() -> None:
    service, _ = _service()
    request = _request(service)
    cancelled = service.cancel(
        request.request_id,
        cancelled_by="approver-1",
        cancelled_at=request.created_at + timedelta(minutes=1),
    )

    assert cancelled.status is OperationStatus.CANCELLED
    with pytest.raises(ValueError, match="only pending operations"):
        service.approve(
            request.request_id,
            approved_by="approver-1",
            approved_at=request.created_at + timedelta(minutes=2),
        )
    with pytest.raises(ValueError, match="only pending or approved"):
        service.cancel(
            request.request_id,
            cancelled_by="approver-1",
            cancelled_at=request.created_at + timedelta(minutes=2),
        )


def test_transition_timestamps_cannot_move_backwards() -> None:
    service, _ = _service()
    request = _request(service)

    with pytest.raises(ValueError, match="cannot precede"):
        service.approve(
            request.request_id,
            approved_by="approver-1",
            approved_at=request.created_at - timedelta(seconds=1),
        )


def test_unknown_request_fails_closed() -> None:
    service, _ = _service()
    with pytest.raises(KeyError, match="unknown operation request"):
        service.get("missing")
