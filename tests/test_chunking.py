from __future__ import annotations

import json
from datetime import date

from enterprise_platform.chunking import ChunkingConfig, chunk_document, chunk_text
from enterprise_platform.document_versions import resolve_document_versions
from enterprise_platform.documents import parse_and_validate_document


def _document(version: int = 1, content: str | None = None) -> dict:
    raw = {
        "document_id": "DOC-RUNBOOK-001",
        "document_version": version,
        "document_type": "RUNBOOK",
        "title": "API triage",
        "source_system": "synthetic-content",
        "source_uri": f"synthetic://runbooks/DOC-RUNBOOK-001/v{version}",
        "account_id": None,
        "department": "platform-engineering",
        "classification": "INTERNAL",
        "allowed_groups": ["support", "platform-engineering"],
        "effective_from": "2026-01-01",
        "effective_to": None,
        "owner": "platform-reliability",
        "updated_at": "2026-09-10T00:00:00Z",
        "content": content
        or (
            "Inspect the regional error rate before escalating the incident.\n\n"
            "Correlate failed requests with the incident timeline and retain evidence.\n\n"
            "Apply rate limiting only after the on-call engineer confirms the retry storm."
        ),
    }
    accepted, violation = parse_and_validate_document(json.dumps(raw))
    assert violation is None
    assert accepted is not None
    return accepted


def test_chunking_respects_paragraphs_size_and_word_aligned_overlap() -> None:
    config = ChunkingConfig(max_chars=105, overlap_chars=20, min_chars=20)

    chunks = chunk_text(_document()["content"], config)

    assert len(chunks) >= 2
    assert all(len(chunk) <= config.max_chars for chunk in chunks)
    assert all(not chunk.startswith(" ") and not chunk.endswith(" ") for chunk in chunks)
    assert "\n\n" not in chunks[0] or chunks[0].count("\n\n") == 1


def test_unchanged_document_reprocessing_produces_identical_chunk_ids() -> None:
    document = _document()

    first = chunk_document(document, ChunkingConfig(max_chars=100, overlap_chars=10))
    second = chunk_document(document, ChunkingConfig(max_chars=100, overlap_chars=10))

    assert first == second
    assert len({chunk["chunk_id"] for chunk in first}) == len(first)


def test_long_unbroken_token_never_exceeds_configured_maximum() -> None:
    config = ChunkingConfig(max_chars=20, overlap_chars=0, min_chars=5)

    chunks = chunk_text("prefix " + "x" * 45, config)

    assert all(len(chunk) <= 20 for chunk in chunks)


def test_content_or_version_change_produces_new_chunk_identity() -> None:
    original = chunk_document(_document(1, "Stable runbook instructions."))[0]
    content_change = chunk_document(_document(1, "Changed runbook instructions."))[0]
    version_change = chunk_document(_document(2, "Stable runbook instructions."))[0]

    assert original["chunk_id"] != content_change["chunk_id"]
    assert original["chunk_id"] != version_change["chunk_id"]


def test_chunks_inherit_active_version_and_access_metadata() -> None:
    old = _document(1, "Historical procedure")
    old["effective_to"] = date(2026, 9, 9)
    current = _document(2, "Current approved procedure")
    current["effective_from"] = date(2026, 9, 10)
    resolved = resolve_document_versions(
        [current], as_of_date=date(2026, 9, 10), prior_versions=[old]
    )

    chunks = [chunk for document in resolved.documents for chunk in chunk_document(document)]

    assert [(chunk["document_version"], chunk["is_active"]) for chunk in chunks] == [
        (1, False),
        (2, True),
    ]
    assert all(chunk["allowed_groups"] == ["platform-engineering", "support"] for chunk in chunks)
    assert all(chunk["source_uri"].startswith("synthetic://") for chunk in chunks)
