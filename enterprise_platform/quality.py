"""Typed normalization and row-level quality rules for structured sources."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any

from enterprise_platform.contracts import (
    ACCOUNT_STATUSES,
    CASE_SEVERITIES,
    CASE_STATUSES,
    PAYMENT_STATUSES,
    PRODUCTS,
    SOURCE_CONTRACTS,
    SUPPORTED_CURRENCIES,
)
from enterprise_platform.schemas import STRUCTURED_SCHEMAS, FieldSpec


@dataclass(frozen=True)
class ContractViolation:
    entity: str
    code: str
    message: str
    raw_record: dict[str, Any]

    def as_record(self) -> dict[str, Any]:
        return asdict(self)


def _required_text(value: Any, field: FieldSpec) -> str | None:
    if value is None or str(value).strip() == "":
        if field.nullable:
            return None
        raise ValueError("required value is empty")
    return str(value).strip()


def _normalize_value(value: Any, field: FieldSpec) -> Any:
    text = _required_text(value, field)
    if text is None:
        return None
    if field.logical_type in {"string", "text"}:
        return text
    if field.logical_type == "integer":
        return int(text)
    if field.logical_type == "decimal":
        with localcontext() as context:
            context.prec = 38
            result = Decimal(text)
        if not result.is_finite():
            raise ValueError("decimal must be finite")
        return result
    if field.logical_type == "boolean":
        normalized = text.lower()
        if normalized not in {"true", "false"}:
            raise ValueError("boolean must be true or false")
        return normalized == "true"
    if field.logical_type == "date":
        return date.fromisoformat(text)
    if field.logical_type == "timestamp":
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        return parsed.astimezone(UTC)
    raise ValueError(f"unsupported logical type: {field.logical_type}")


def normalize_structured_row(entity: str, raw_record: dict[str, Any]) -> dict[str, Any]:
    """Normalize one source row according to its exact versioned field schema."""

    if entity not in STRUCTURED_SCHEMAS:
        raise ValueError(f"unknown structured entity: {entity}")
    expected = {field.name for field in STRUCTURED_SCHEMAS[entity]}
    if set(raw_record) != expected:
        missing = sorted(expected - set(raw_record))
        extra = sorted(set(raw_record) - expected)
        raise ValueError(f"schema mismatch: missing={missing}, extra={extra}")
    return {
        field.name: _normalize_value(raw_record[field.name], field)
        for field in STRUCTURED_SCHEMAS[entity]
    }


def _validate_domains(entity: str, row: dict[str, Any]) -> None:
    if entity == "accounts" and row["account_status"] not in ACCOUNT_STATUSES:
        raise ValueError("account_status is outside the contracted domain")
    if entity == "invoices":
        if row["payment_status"] not in PAYMENT_STATUSES:
            raise ValueError("payment_status is outside the contracted domain")
        if row["currency"] not in SUPPORTED_CURRENCIES:
            raise ValueError("currency is outside the contracted domain")
    if entity == "support_cases":
        if row["severity"] not in CASE_SEVERITIES:
            raise ValueError("severity is outside the contracted domain")
        if row["status"] not in CASE_STATUSES:
            raise ValueError("status is outside the contracted domain")
    if entity == "product_usage_daily" and row["product"] not in PRODUCTS:
        raise ValueError("product is outside the contracted domain")


def _validate_measures(entity: str, row: dict[str, Any]) -> None:
    if entity == "accounts" and row["annual_contract_value"] < 0:
        raise ValueError("annual_contract_value cannot be negative")
    if entity == "invoices":
        if row["amount"] < 0 or row["days_past_due"] < 0:
            raise ValueError("invoice monetary and aging measures cannot be negative")
    if entity == "support_cases":
        if row["resolution_minutes"] is not None and row["resolution_minutes"] < 0:
            raise ValueError("resolution_minutes cannot be negative")
        if row["status"] in {"RESOLVED", "CLOSED"} and row["closed_at"] is None:
            raise ValueError("resolved support cases require closed_at")
    if entity == "product_usage_daily":
        nonnegative = ("active_users", "api_calls", "failed_requests")
        if any(row[field] < 0 for field in nonnegative):
            raise ValueError("usage counts cannot be negative")
        if row["failed_requests"] > row["api_calls"]:
            raise ValueError("failed_requests cannot exceed api_calls")
        if not Decimal("0") <= row["availability_pct"] <= Decimal("100"):
            raise ValueError("availability_pct must be between 0 and 100")


def validate_structured_row(
    entity: str,
    raw_record: dict[str, Any],
    *,
    business_date: date,
    max_staleness_days: int = 7,
) -> tuple[dict[str, Any] | None, ContractViolation | None]:
    """Return exactly one accepted typed row or one quarantine reason."""

    try:
        row = normalize_structured_row(entity, raw_record)
        _validate_domains(entity, row)
        _validate_measures(entity, row)
        age_days = (business_date - row["updated_at"].date()).days
        if age_days > max_staleness_days:
            raise ValueError(f"updated_at is stale by {age_days} days")
        if age_days < 0:
            raise ValueError("updated_at is later than the business date")
    except (InvalidOperation, TypeError, ValueError) as exc:
        return None, ContractViolation(
            entity=entity,
            code="CONTRACT_VIOLATION",
            message=str(exc),
            raw_record=dict(raw_record),
        )
    return row, None


def natural_key(entity: str, row: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(row[field] for field in SOURCE_CONTRACTS[entity].natural_key)
