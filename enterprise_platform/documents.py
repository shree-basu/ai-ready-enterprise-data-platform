"""Document-envelope validation, normalization, and durable content identity."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from typing import Any

from enterprise_platform.contracts import AccessGroup, Classification, DocumentType
from enterprise_platform.schemas import DOCUMENT_SCHEMA, field_names


@dataclass(frozen=True)
class DocumentViolation:
    code: str
    message: str
    raw_record: dict[str, Any] | str

    def as_record(self) -> dict[str, Any]:
        return {"entity": "documents", **asdict(self)}


def normalize_content(content: str) -> str:
    """Canonicalize line endings and outer whitespace without rewriting source prose."""

    return content.replace("\r\n", "\n").replace("\r", "\n").strip()


def content_hash(content: str) -> str:
    return hashlib.sha256(normalize_content(content).encode("utf-8")).hexdigest()


def _required_text(value: Any, name: str, *, nullable: bool = False) -> str | None:
    if value is None or str(value).strip() == "":
        if nullable:
            return None
        raise ValueError(f"{name} is required")
    return str(value).strip()


def _date(value: Any, name: str, *, nullable: bool = False) -> date | None:
    text = _required_text(value, name, nullable=nullable)
    return None if text is None else date.fromisoformat(text)


def _timestamp(value: Any, name: str) -> datetime:
    text = _required_text(value, name)
    assert text is not None
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone")
    return parsed.astimezone(UTC)


def normalize_document(raw_record: dict[str, Any]) -> dict[str, Any]:
    """Validate the complete governed envelope and return canonical typed metadata."""

    expected = set(field_names(DOCUMENT_SCHEMA))
    if set(raw_record) != expected:
        missing = sorted(expected - set(raw_record))
        extra = sorted(set(raw_record) - expected)
        raise ValueError(f"document schema mismatch: missing={missing}, extra={extra}")

    version = raw_record["document_version"]
    if isinstance(version, bool):
        raise ValueError("document_version must be a positive integer")
    version = int(version)
    if version < 1:
        raise ValueError("document_version must be a positive integer")

    document_type = _required_text(raw_record["document_type"], "document_type")
    classification = _required_text(raw_record["classification"], "classification")
    if document_type not in {value.value for value in DocumentType}:
        raise ValueError("document_type is outside the contracted domain")
    if classification not in {value.value for value in Classification}:
        raise ValueError("classification is outside the contracted domain")

    groups = raw_record["allowed_groups"]
    if not isinstance(groups, list) or any(not isinstance(group, str) for group in groups):
        raise ValueError("allowed_groups must be an array of strings")
    groups = sorted(set(group.strip() for group in groups if group.strip()))
    allowed = {value.value for value in AccessGroup}
    if not set(groups) <= allowed:
        raise ValueError("allowed_groups contains an unknown group")
    if classification != Classification.PUBLIC and not groups:
        raise ValueError("non-public documents require at least one allowed group")

    effective_from = _date(raw_record["effective_from"], "effective_from")
    effective_to = _date(raw_record["effective_to"], "effective_to", nullable=True)
    assert effective_from is not None
    if effective_to is not None and effective_to < effective_from:
        raise ValueError("effective_to cannot precede effective_from")
    content = _required_text(raw_record["content"], "content")
    assert content is not None
    normalized_content = normalize_content(content)

    normalized = {
        "document_id": _required_text(raw_record["document_id"], "document_id"),
        "document_version": version,
        "document_type": document_type,
        "title": _required_text(raw_record["title"], "title"),
        "source_system": _required_text(raw_record["source_system"], "source_system"),
        "source_uri": _required_text(raw_record["source_uri"], "source_uri"),
        "account_id": _required_text(raw_record["account_id"], "account_id", nullable=True),
        "department": _required_text(raw_record["department"], "department"),
        "classification": classification,
        "allowed_groups": groups,
        "effective_from": effective_from,
        "effective_to": effective_to,
        "owner": _required_text(raw_record["owner"], "owner"),
        "updated_at": _timestamp(raw_record["updated_at"], "updated_at"),
        "content": normalized_content,
        "content_hash": content_hash(normalized_content),
    }
    return normalized


def parse_and_validate_document(
    raw_line: str,
) -> tuple[dict[str, Any] | None, DocumentViolation | None]:
    """Return one valid normalized document or one explicit quarantine reason."""

    try:
        raw = json.loads(raw_line)
        if not isinstance(raw, dict):
            raise ValueError("document line must contain a JSON object")
        return normalize_document(raw), None
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raw_record: dict[str, Any] | str
        if "raw" in locals() and isinstance(raw, dict):
            raw_record = raw
        else:
            raw_record = raw_line
        return None, DocumentViolation(
            code="DOCUMENT_CONTRACT_VIOLATION",
            message=str(exc),
            raw_record=raw_record,
        )
