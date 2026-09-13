from __future__ import annotations

import pytest

from enterprise_platform.lexical_index import LocalLexicalIndex, tokenize


def _row(chunk_id: str, text: str, *, active: bool = True) -> dict[str, object]:
    return {
        "chunk_id": chunk_id,
        "chunk_text": text,
        "is_active": active,
        "classification": "INTERNAL",
        "allowed_groups": ["support"],
    }


def test_bm25_ranks_exact_enterprise_terms_deterministically() -> None:
    index = LocalLexicalIndex(
        [
            _row("api", "API retry storm incident response and API rate limiting"),
            _row("invoice", "overdue invoice account review policy"),
            _row("sla", "SEV1 incident acknowledgement target"),
        ]
    )

    hits = index.search("API retry storm", k=3)

    assert [hit.chunk["chunk_id"] for hit in hits] == ["api"]
    assert hits[0].score > 0


def test_metadata_filter_is_applied_before_lexical_statistics_and_ranking() -> None:
    index = LocalLexicalIndex(
        [
            _row("restricted", "unique investigation phrase"),
            _row("allowed", "investigation guidance"),
        ]
    )

    hits = index.search(
        "unique investigation phrase",
        metadata_filter=lambda row: row["chunk_id"] == "allowed",
    )

    assert [hit.chunk["chunk_id"] for hit in hits] == ["allowed"]


def test_publication_is_replay_safe_ignores_inactive_and_rejects_conflicts() -> None:
    row = _row("one", "stable text")
    index = LocalLexicalIndex([row, _row("old", "inactive", active=False)])

    update = index.add([row])
    assert (index.size, update.inserted, update.replayed) == (1, 0, 1)

    with pytest.raises(ValueError, match="conflicting active text"):
        index.add([{**row, "chunk_text": "mutated text"}])
    assert index.size == 1


def test_tokenization_and_empty_query_behavior_are_explicit() -> None:
    index = LocalLexicalIndex([_row("one", "Availability is 99.9 percent")])

    assert tokenize("SEV1/API's 99.9%") == ["sev1", "api", "s", "99", "9"]
    assert index.search("---") == []
    with pytest.raises(ValueError, match="k must be positive"):
        index.search("availability", k=0)
