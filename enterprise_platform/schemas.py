"""Explicit, engine-neutral field schemas for immutable source files."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

LogicalType = Literal[
    "string",
    "integer",
    "decimal",
    "boolean",
    "date",
    "timestamp",
    "string_array",
    "text",
]


@dataclass(frozen=True)
class FieldSpec:
    name: str
    logical_type: LogicalType
    nullable: bool = False


STRUCTURED_SCHEMAS: dict[str, tuple[FieldSpec, ...]] = {
    "accounts": (
        FieldSpec("account_id", "string"),
        FieldSpec("account_name", "string"),
        FieldSpec("segment", "string"),
        FieldSpec("region", "string"),
        FieldSpec("industry", "string"),
        FieldSpec("account_status", "string"),
        FieldSpec("annual_contract_value", "decimal"),
        FieldSpec("contract_id", "string"),
        FieldSpec("owner_team", "string"),
        FieldSpec("updated_at", "timestamp"),
    ),
    "invoices": (
        FieldSpec("invoice_id", "string"),
        FieldSpec("account_id", "string"),
        FieldSpec("invoice_date", "date"),
        FieldSpec("amount", "decimal"),
        FieldSpec("currency", "string"),
        FieldSpec("payment_status", "string"),
        FieldSpec("days_past_due", "integer"),
        FieldSpec("updated_at", "timestamp"),
    ),
    "support_cases": (
        FieldSpec("case_id", "string"),
        FieldSpec("account_id", "string"),
        FieldSpec("opened_at", "timestamp"),
        FieldSpec("closed_at", "timestamp", nullable=True),
        FieldSpec("severity", "string"),
        FieldSpec("category", "string"),
        FieldSpec("status", "string"),
        FieldSpec("sla_breached", "boolean"),
        FieldSpec("resolution_minutes", "integer", nullable=True),
        FieldSpec("updated_at", "timestamp"),
    ),
    "product_usage_daily": (
        FieldSpec("account_id", "string"),
        FieldSpec("usage_date", "date"),
        FieldSpec("product", "string"),
        FieldSpec("active_users", "integer"),
        FieldSpec("api_calls", "integer"),
        FieldSpec("failed_requests", "integer"),
        FieldSpec("availability_pct", "decimal"),
        FieldSpec("updated_at", "timestamp"),
    ),
}

DOCUMENT_SCHEMA: tuple[FieldSpec, ...] = (
    FieldSpec("document_id", "string"),
    FieldSpec("document_version", "integer"),
    FieldSpec("document_type", "string"),
    FieldSpec("title", "string"),
    FieldSpec("source_system", "string"),
    FieldSpec("source_uri", "string"),
    FieldSpec("account_id", "string", nullable=True),
    FieldSpec("department", "string"),
    FieldSpec("classification", "string"),
    FieldSpec("allowed_groups", "string_array"),
    FieldSpec("effective_from", "date"),
    FieldSpec("effective_to", "date", nullable=True),
    FieldSpec("owner", "string"),
    FieldSpec("updated_at", "timestamp"),
    FieldSpec("content", "text"),
)


def field_names(schema: tuple[FieldSpec, ...]) -> tuple[str, ...]:
    return tuple(field.name for field in schema)
