from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from enterprise_platform.quality import natural_key, validate_structured_row


def _account(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "account_id": " A-1001 ",
        "account_name": "Synthetic Enterprise",
        "segment": "ENTERPRISE",
        "region": "AMER",
        "industry": "RETAIL",
        "account_status": "ACTIVE",
        "annual_contract_value": "100000.25",
        "contract_id": "CTR-1001",
        "owner_team": "TEAM_NORTH",
        "updated_at": "2026-09-10T01:00:00Z",
    }
    row.update(overrides)
    return row


def test_valid_row_is_typed_and_normalized() -> None:
    accepted, violation = validate_structured_row(
        "accounts", _account(), business_date=date(2026, 9, 10)
    )

    assert violation is None
    assert accepted is not None
    assert accepted["account_id"] == "A-1001"
    assert accepted["annual_contract_value"] == Decimal("100000.25")
    assert accepted["updated_at"] == datetime(2026, 9, 10, 1, tzinfo=UTC)
    assert natural_key("accounts", accepted) == ("A-1001",)


def test_schema_mismatch_is_quarantined_without_silent_drop() -> None:
    raw = _account()
    del raw["contract_id"]

    accepted, violation = validate_structured_row("accounts", raw, business_date=date(2026, 9, 10))

    assert accepted is None
    assert violation is not None
    assert violation.code == "CONTRACT_VIOLATION"
    assert "schema mismatch" in violation.message
    assert violation.raw_record == raw


def test_invalid_domain_and_stale_rows_are_quarantined() -> None:
    invalid, invalid_violation = validate_structured_row(
        "accounts", _account(account_status="UNKNOWN"), business_date=date(2026, 9, 10)
    )
    stale, stale_violation = validate_structured_row(
        "accounts",
        _account(updated_at="2026-08-01T00:00:00Z"),
        business_date=date(2026, 9, 10),
    )

    assert invalid is stale is None
    assert invalid_violation is not None
    assert "account_status" in invalid_violation.message
    assert stale_violation is not None
    assert "stale" in stale_violation.message


def test_invalid_decimal_and_naive_timestamp_are_quarantined() -> None:
    invalid_decimal, decimal_violation = validate_structured_row(
        "accounts",
        _account(annual_contract_value="not-a-number"),
        business_date=date(2026, 9, 10),
    )
    naive_time, time_violation = validate_structured_row(
        "accounts",
        _account(updated_at="2026-09-10T01:00:00"),
        business_date=date(2026, 9, 10),
    )

    assert invalid_decimal is naive_time is None
    assert decimal_violation is not None
    assert time_violation is not None
    assert "timezone" in time_violation.message
