from enterprise_platform.contracts import (
    SOURCE_CONTRACTS,
    AccessGroup,
    Classification,
    DocumentType,
)
from enterprise_platform.schemas import DOCUMENT_SCHEMA, STRUCTURED_SCHEMAS, field_names


def test_source_contracts_define_exact_expected_files_and_grains() -> None:
    assert set(SOURCE_CONTRACTS) == {
        "accounts",
        "invoices",
        "support_cases",
        "product_usage_daily",
        "documents",
    }
    assert {contract.relative_path for contract in SOURCE_CONTRACTS.values()} == {
        "structured/accounts.csv",
        "structured/invoices.csv",
        "structured/support_cases.csv",
        "structured/product_usage_daily.csv",
        "documents/documents.jsonl",
    }
    assert SOURCE_CONTRACTS["product_usage_daily"].natural_key == (
        "account_id",
        "usage_date",
        "product",
    )
    assert SOURCE_CONTRACTS["documents"].natural_key == (
        "document_id",
        "document_version",
    )


def test_structured_schemas_are_explicit_and_match_the_contracts() -> None:
    assert set(STRUCTURED_SCHEMAS) == set(SOURCE_CONTRACTS) - {"documents"}
    assert field_names(STRUCTURED_SCHEMAS["accounts"])[0:2] == (
        "account_id",
        "account_name",
    )
    assert field_names(STRUCTURED_SCHEMAS["invoices"]) == (
        "invoice_id",
        "account_id",
        "invoice_date",
        "amount",
        "currency",
        "payment_status",
        "days_past_due",
        "updated_at",
    )
    support_fields = {field.name: field for field in STRUCTURED_SCHEMAS["support_cases"]}
    assert support_fields["closed_at"].nullable is True
    assert support_fields["resolution_minutes"].nullable is True


def test_document_envelope_requires_governance_and_version_metadata() -> None:
    names = set(field_names(DOCUMENT_SCHEMA))
    assert {
        "document_id",
        "document_version",
        "classification",
        "allowed_groups",
        "effective_from",
        "effective_to",
        "source_uri",
        "owner",
        "content",
    } <= names
    assert {value.value for value in Classification} == {
        "PUBLIC",
        "INTERNAL",
        "RESTRICTED",
    }
    assert {value.value for value in AccessGroup} == {
        "support",
        "finance",
        "account-management",
        "platform-engineering",
        "legal",
    }
    assert {value.value for value in DocumentType} == {
        "CONTRACT",
        "SLA",
        "RUNBOOK",
        "POLICY",
        "INCIDENT_POSTMORTEM",
    }
