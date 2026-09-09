"""Versioned source, document, and domain contracts for synthetic enterprise data."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

SCHEMA_VERSION = "1.0"


class Classification(StrEnum):
    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    RESTRICTED = "RESTRICTED"


class AccessGroup(StrEnum):
    SUPPORT = "support"
    FINANCE = "finance"
    ACCOUNT_MANAGEMENT = "account-management"
    PLATFORM_ENGINEERING = "platform-engineering"
    LEGAL = "legal"


class DocumentType(StrEnum):
    CONTRACT = "CONTRACT"
    SLA = "SLA"
    RUNBOOK = "RUNBOOK"
    POLICY = "POLICY"
    INCIDENT_POSTMORTEM = "INCIDENT_POSTMORTEM"


@dataclass(frozen=True)
class SourceContract:
    name: str
    relative_path: str
    file_format: str
    grain: str
    natural_key: tuple[str, ...]


SOURCE_CONTRACTS: dict[str, SourceContract] = {
    "accounts": SourceContract(
        name="accounts",
        relative_path="structured/accounts.csv",
        file_format="csv",
        grain="one enterprise customer account",
        natural_key=("account_id",),
    ),
    "invoices": SourceContract(
        name="invoices",
        relative_path="structured/invoices.csv",
        file_format="csv",
        grain="one invoice",
        natural_key=("invoice_id",),
    ),
    "support_cases": SourceContract(
        name="support_cases",
        relative_path="structured/support_cases.csv",
        file_format="csv",
        grain="one support case",
        natural_key=("case_id",),
    ),
    "product_usage_daily": SourceContract(
        name="product_usage_daily",
        relative_path="structured/product_usage_daily.csv",
        file_format="csv",
        grain="one account, usage date, and product",
        natural_key=("account_id", "usage_date", "product"),
    ),
    "documents": SourceContract(
        name="documents",
        relative_path="documents/documents.jsonl",
        file_format="jsonl",
        grain="one document envelope version",
        natural_key=("document_id", "document_version"),
    ),
}

ACCOUNT_STATUSES = frozenset({"ACTIVE", "AT_RISK", "SUSPENDED", "CLOSED"})
PAYMENT_STATUSES = frozenset({"PAID", "OPEN", "OVERDUE", "DISPUTED"})
CASE_SEVERITIES = frozenset({"SEV1", "SEV2", "SEV3", "SEV4"})
CASE_STATUSES = frozenset({"OPEN", "IN_PROGRESS", "RESOLVED", "CLOSED"})
SUPPORTED_CURRENCIES = frozenset({"USD"})
PRODUCTS = frozenset({"CORE_API", "ANALYTICS", "WORKFLOW"})
