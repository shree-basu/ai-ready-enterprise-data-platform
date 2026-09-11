from __future__ import annotations

import json
from datetime import UTC, date, datetime

from enterprise_platform.documents import content_hash, parse_and_validate_document


def _document(**overrides: object) -> dict[str, object]:
    document: dict[str, object] = {
        "document_id": "DOC-SLA-001",
        "document_version": 1,
        "document_type": "SLA",
        "title": "Customer SLA",
        "source_system": "synthetic-content",
        "source_uri": "synthetic://sla/DOC-SLA-001/v1",
        "account_id": "A-1001",
        "department": "support",
        "classification": "INTERNAL",
        "allowed_groups": ["support", "account-management", "support"],
        "effective_from": "2026-01-01",
        "effective_to": None,
        "owner": "support-operations",
        "updated_at": "2026-09-10T02:00:00Z",
        "content": " First paragraph.\r\n\r\nSecond paragraph. ",
    }
    document.update(overrides)
    return document


def test_valid_document_normalizes_governance_metadata_and_content_identity() -> None:
    accepted, violation = parse_and_validate_document(json.dumps(_document()))

    assert violation is None
    assert accepted is not None
    assert accepted["effective_from"] == date(2026, 1, 1)
    assert accepted["updated_at"] == datetime(2026, 9, 10, 2, tzinfo=UTC)
    assert accepted["allowed_groups"] == ["account-management", "support"]
    assert accepted["content"] == "First paragraph.\n\nSecond paragraph."
    assert accepted["content_hash"] == content_hash(accepted["content"])


def test_malformed_json_is_quarantined_with_original_line() -> None:
    malformed = '{"document_id":"BROKEN",not-json'

    accepted, violation = parse_and_validate_document(malformed)

    assert accepted is None
    assert violation is not None
    assert violation.code == "DOCUMENT_CONTRACT_VIOLATION"
    assert violation.raw_record == malformed


def test_unknown_group_and_missing_restricted_acl_are_rejected() -> None:
    unknown, unknown_violation = parse_and_validate_document(
        json.dumps(_document(allowed_groups=["executive-secret"]))
    )
    missing, missing_violation = parse_and_validate_document(
        json.dumps(_document(classification="RESTRICTED", allowed_groups=[]))
    )

    assert unknown is missing is None
    assert unknown_violation is not None
    assert "unknown group" in unknown_violation.message
    assert missing_violation is not None
    assert "require at least one" in missing_violation.message


def test_invalid_version_dates_and_empty_content_are_rejected() -> None:
    invalid_cases = (
        _document(document_version=0),
        _document(effective_from="2026-09-10", effective_to="2026-09-09"),
        _document(content="  "),
    )

    for document in invalid_cases:
        accepted, violation = parse_and_validate_document(json.dumps(document))
        assert accepted is None
        assert violation is not None
