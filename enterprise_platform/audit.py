"""Content-minimizing, deterministic local audit events."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any


class AuditEventType(StrEnum):
    PIPELINE_RUN = "PIPELINE_RUN"
    DATA_QUALITY = "DATA_QUALITY"
    QUARANTINE = "QUARANTINE"
    RETRIEVAL = "RETRIEVAL"
    SQL_QUERY = "SQL_QUERY"
    AGENT_TOOL_CALL = "AGENT_TOOL_CALL"
    AUTHORIZATION = "AUTHORIZATION"
    OPERATION_REQUEST = "OPERATION_REQUEST"


_SENSITIVE_DETAIL_KEYS = frozenset(
    {
        "content",
        "chunk_text",
        "raw_record",
        "sql",
        "prompt",
        "token",
        "secret",
        "credential",
    }
)


def hash_actor(actor_id: str) -> str:
    normalized = actor_id.strip()
    if not normalized:
        raise ValueError("actor_id is required")
    return hashlib.sha256(normalized.encode()).hexdigest()


def _aware_timestamp(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("occurred_at must include a timezone")
    return value.astimezone(UTC)


def _canonical_details(details: dict[str, Any]) -> dict[str, Any]:
    denied = _SENSITIVE_DETAIL_KEYS.intersection(key.casefold() for key in details)
    if denied:
        raise ValueError("audit details cannot contain sensitive content fields")
    try:
        encoded = json.dumps(details, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("audit details must be finite JSON values") from exc
    decoded = json.loads(encoded)
    if not isinstance(decoded, dict):
        raise ValueError("audit details must be an object")
    return decoded


@dataclass(frozen=True)
class AuditEvent:
    event_id: str
    event_type: AuditEventType
    occurred_at: datetime
    actor_hash: str
    action: str
    decision: str
    object_ids: tuple[str, ...]
    details: dict[str, Any]


def create_audit_event(
    event_type: AuditEventType,
    *,
    occurred_at: datetime,
    actor_id: str,
    action: str,
    decision: str,
    object_ids: tuple[str, ...] = (),
    details: dict[str, Any] | None = None,
) -> AuditEvent:
    occurred = _aware_timestamp(occurred_at)
    normalized_action = action.strip()
    normalized_decision = decision.strip()
    if not normalized_action or not normalized_decision:
        raise ValueError("audit action and decision are required")
    if any(not value.strip() for value in object_ids):
        raise ValueError("audit object IDs cannot be blank")
    canonical_details = _canonical_details(details or {})
    payload = {
        "event_type": event_type.value,
        "occurred_at": occurred.isoformat(),
        "actor_hash": hash_actor(actor_id),
        "action": normalized_action,
        "decision": normalized_decision,
        "object_ids": sorted(set(object_ids)),
        "details": canonical_details,
    }
    event_id = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return AuditEvent(
        event_id=event_id,
        event_type=event_type,
        occurred_at=occurred,
        actor_hash=payload["actor_hash"],
        action=normalized_action,
        decision=normalized_decision,
        object_ids=tuple(payload["object_ids"]),
        details=canonical_details,
    )


def _event_record(event: AuditEvent) -> dict[str, Any]:
    record = asdict(event)
    record["event_type"] = event.event_type.value
    record["occurred_at"] = event.occurred_at.isoformat()
    record["object_ids"] = list(event.object_ids)
    return record


class LocalAuditLog:
    """Append-only in-memory ledger with deterministic local JSONL export."""

    def __init__(self) -> None:
        self._events: dict[str, AuditEvent] = {}

    def append(self, event: AuditEvent) -> bool:
        existing = self._events.get(event.event_id)
        if existing is not None:
            if existing != event:
                raise ValueError("audit event ID conflicts with existing content")
            return False
        self._events[event.event_id] = event
        return True

    @property
    def events(self) -> tuple[AuditEvent, ...]:
        return tuple(
            sorted(self._events.values(), key=lambda event: (event.occurred_at, event.event_id))
        )

    def by_type(self, event_type: AuditEventType) -> tuple[AuditEvent, ...]:
        return tuple(event for event in self.events if event.event_type is event_type)

    def export_jsonl(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = "".join(
            json.dumps(_event_record(event), sort_keys=True, separators=(",", ":")) + "\n"
            for event in self.events
        )
        staging = path.with_suffix(path.suffix + ".tmp")
        staging.write_text(payload, encoding="utf-8", newline="\n")
        staging.replace(path)
        return path

    @classmethod
    def from_jsonl(cls, path: Path) -> LocalAuditLog:
        log = cls()
        if not path.exists():
            return log
        for line in path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            event = AuditEvent(
                event_id=record["event_id"],
                event_type=AuditEventType(record["event_type"]),
                occurred_at=_aware_timestamp(datetime.fromisoformat(record["occurred_at"])),
                actor_hash=record["actor_hash"],
                action=record["action"],
                decision=record["decision"],
                object_ids=tuple(record["object_ids"]),
                details=_canonical_details(record["details"]),
            )
            expected = create_audit_event(
                event.event_type,
                occurred_at=event.occurred_at,
                actor_id="verification-placeholder",
                action=event.action,
                decision=event.decision,
                object_ids=event.object_ids,
                details=event.details,
            )
            payload = _event_record(event)
            payload.pop("event_id")
            expected_payload = _event_record(expected)
            expected_payload.pop("event_id")
            expected_payload["actor_hash"] = event.actor_hash
            expected_id = hashlib.sha256(
                json.dumps(expected_payload, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            if event.event_id != expected_id or payload != expected_payload:
                raise ValueError("stored audit event failed integrity validation")
            log.append(event)
        return log
