from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from enterprise_platform.audit import (
    AuditEventType,
    LocalAuditLog,
    create_audit_event,
    hash_actor,
)


def _event():
    return create_audit_event(
        AuditEventType.RETRIEVAL,
        occurred_at=datetime(2026, 9, 17, 8, tzinfo=UTC),
        actor_id="analyst-1",
        action="search_enterprise_knowledge",
        decision="ALLOWED",
        object_ids=("DOC-2", "DOC-1", "DOC-1"),
        details={"result_count": 2, "retrieval_mode": "hybrid"},
    )


def test_event_identity_is_deterministic_and_actor_is_hashed() -> None:
    first = _event()
    second = _event()

    assert first == second
    assert first.actor_hash == hash_actor("analyst-1")
    assert first.actor_hash != "analyst-1"
    assert first.object_ids == ("DOC-1", "DOC-2")


def test_append_is_replay_safe_and_filterable() -> None:
    log = LocalAuditLog()

    assert log.append(_event()) is True
    assert log.append(_event()) is False
    assert log.by_type(AuditEventType.RETRIEVAL) == (_event(),)


@pytest.mark.parametrize("key", ["content", "chunk_text", "raw_record", "sql", "prompt"])
def test_sensitive_content_fields_are_rejected(key: str) -> None:
    with pytest.raises(ValueError, match="sensitive content"):
        create_audit_event(
            AuditEventType.AGENT_TOOL_CALL,
            occurred_at=datetime(2026, 9, 17, tzinfo=UTC),
            actor_id="agent-1",
            action="tool",
            decision="COMPLETED",
            details={key: "must not be logged"},
        )


def test_jsonl_round_trip_and_tamper_detection(tmp_path) -> None:
    path = tmp_path / "audit" / "events.jsonl"
    log = LocalAuditLog()
    log.append(_event())
    log.export_jsonl(path)

    restored = LocalAuditLog.from_jsonl(path)
    assert restored.events == log.events

    record = json.loads(path.read_text(encoding="utf-8"))
    record["decision"] = "DENIED"
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="integrity validation"):
        LocalAuditLog.from_jsonl(path)


def test_naive_timestamp_and_non_json_details_are_rejected() -> None:
    with pytest.raises(ValueError, match="timezone"):
        create_audit_event(
            AuditEventType.SQL_QUERY,
            occurred_at=datetime(2026, 9, 17),
            actor_id="analyst-1",
            action="query",
            decision="ALLOWED",
        )
    with pytest.raises(ValueError, match="finite JSON"):
        create_audit_event(
            AuditEventType.SQL_QUERY,
            occurred_at=datetime(2026, 9, 17, tzinfo=UTC),
            actor_id="analyst-1",
            action="query",
            decision="ALLOWED",
            details={"latency": float("nan")},
        )
