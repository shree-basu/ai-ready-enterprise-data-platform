from __future__ import annotations

import json
from datetime import date

from enterprise_platform.document_versions import resolve_document_versions
from enterprise_platform.documents import parse_and_validate_document


def _document(version: int, content: str, **overrides: object) -> dict:
    raw: dict[str, object] = {
        "document_id": "DOC-SLA-001",
        "document_version": version,
        "document_type": "SLA",
        "title": "Customer SLA",
        "source_system": "synthetic-content",
        "source_uri": f"synthetic://sla/DOC-SLA-001/v{version}",
        "account_id": "A-1001",
        "department": "support",
        "classification": "INTERNAL",
        "allowed_groups": ["support"],
        "effective_from": "2026-01-01",
        "effective_to": None,
        "owner": "support-operations",
        "updated_at": f"2026-09-{version + 1:02d}T00:00:00Z",
        "content": content,
    }
    raw.update(overrides)
    accepted, violation = parse_and_validate_document(json.dumps(raw))
    assert violation is None
    assert accepted is not None
    return accepted


def test_new_effective_version_deactivates_historical_chunks_source() -> None:
    old = _document(1, "Original SLA", effective_to="2026-09-09")
    new = _document(2, "Updated SLA", effective_from="2026-09-10")

    result = resolve_document_versions([new], as_of_date=date(2026, 9, 10), prior_versions=[old])

    assert [(row["document_version"], row["is_active"]) for row in result.documents] == [
        (1, False),
        (2, True),
    ]
    assert result.documents[0]["document_version_id"] != result.documents[1]["document_version_id"]


def test_exact_document_replay_is_idempotent() -> None:
    document = _document(1, "Stable content")

    result = resolve_document_versions(
        [document, document],
        as_of_date=date(2026, 9, 10),
        prior_versions=[document],
    )

    assert len(result.documents) == 1
    assert result.replayed_documents == 2
    assert result.quarantined == []


def test_same_version_content_mutation_fails_closed() -> None:
    prior = _document(1, "Approved wording")
    mutation = _document(1, "Changed wording without a new version")

    result = resolve_document_versions(
        [mutation], as_of_date=date(2026, 9, 10), prior_versions=[prior]
    )

    assert [row["content"] for row in result.documents] == ["Approved wording"]
    assert len(result.quarantined) == 1
    assert result.quarantined[0].code == "DOCUMENT_VERSION_MUTATION"


def test_older_unseen_version_is_quarantined_as_stale() -> None:
    current = _document(2, "Current version")
    stale = _document(1, "Previously unseen stale version")

    result = resolve_document_versions(
        [stale], as_of_date=date(2026, 9, 10), prior_versions=[current]
    )

    assert len(result.documents) == 1
    assert result.documents[0]["document_version"] == 2
    assert result.quarantined[0].code == "STALE_DOCUMENT_VERSION"
