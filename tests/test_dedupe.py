from __future__ import annotations

from datetime import UTC, datetime

from enterprise_platform.dedupe import (
    canonical_record_hash,
    deterministic_deduplicate,
    quarantine_orphan_accounts,
)


def _account(name: str, updated_minute: int) -> dict:
    return {
        "account_id": "A-1001",
        "account_name": name,
        "updated_at": datetime(2026, 9, 10, 1, updated_minute, tzinfo=UTC),
    }


def test_latest_updated_at_wins_and_duplicate_is_quarantined() -> None:
    old = _account("Old name", 1)
    latest = _account("Current name", 2)

    accepted, quarantined = deterministic_deduplicate("accounts", [old, latest])

    assert accepted == [latest]
    assert len(quarantined) == 1
    assert quarantined[0].code == "DUPLICATE_NATURAL_KEY"
    assert quarantined[0].raw_record == old


def test_tie_break_and_output_are_independent_of_input_order() -> None:
    first = _account("Alpha", 1)
    second = _account("Beta", 1)

    forward = deterministic_deduplicate("accounts", [first, second])
    reverse = deterministic_deduplicate("accounts", [second, first])

    expected = max([first, second], key=canonical_record_hash)
    assert forward == reverse
    assert forward[0] == [expected]


def test_orphan_reference_is_quarantined_and_not_silently_dropped() -> None:
    valid = {"invoice_id": "INV-1", "account_id": "A-1001"}
    orphan = {"invoice_id": "INV-2", "account_id": "A-9999"}

    accepted, quarantined = quarantine_orphan_accounts(
        "invoices", [valid, orphan], valid_account_ids={"A-1001"}
    )

    assert accepted == [valid]
    assert len(quarantined) == 1
    assert quarantined[0].code == "ORPHAN_ACCOUNT_REFERENCE"
    assert quarantined[0].raw_record == orphan
