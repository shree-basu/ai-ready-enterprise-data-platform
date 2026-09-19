"""Transactional, replay-safe local analytical serving with DuckDB."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb

from enterprise_platform.structured import ReplayConflict, StructuredBatchResult


@dataclass(frozen=True)
class PublicationResult:
    pipeline_run_id: str
    inserted_rows: int
    replayed: bool


def _json_value(value: Any) -> Any:
    if isinstance(value, date | datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in sorted(value.items())}
    if isinstance(value, list | tuple):
        return [_json_value(item) for item in value]
    return value


def _batch_fingerprint(result: StructuredBatchResult) -> str:
    payload = {
        "business_date": result.business_date,
        "batch_id": result.batch_id,
        "pipeline_run_id": result.pipeline_run_id,
        "accepted": result.accepted,
    }
    encoded = json.dumps(_json_value(payload), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


class LocalAnalyticsStore:
    """Own an explicit DuckDB connection and publish one immutable batch atomically."""

    def __init__(self, database: str | Path = ":memory:") -> None:
        self.connection = duckdb.connect(str(database))
        self._create_schema()

    def _create_schema(self) -> None:
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS pipeline_runs (
                pipeline_run_id VARCHAR PRIMARY KEY,
                business_date DATE NOT NULL,
                batch_id VARCHAR NOT NULL,
                batch_fingerprint VARCHAR NOT NULL,
                published_at TIMESTAMP NOT NULL DEFAULT current_timestamp
            );

            CREATE TABLE IF NOT EXISTS curated_accounts (
                pipeline_run_id VARCHAR NOT NULL,
                account_id VARCHAR NOT NULL,
                account_name VARCHAR NOT NULL,
                segment VARCHAR NOT NULL,
                region VARCHAR NOT NULL,
                industry VARCHAR NOT NULL,
                account_status VARCHAR NOT NULL,
                annual_contract_value DECIMAL(18, 2) NOT NULL,
                contract_id VARCHAR NOT NULL,
                owner_team VARCHAR NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL,
                PRIMARY KEY (pipeline_run_id, account_id)
            );

            CREATE TABLE IF NOT EXISTS curated_invoices (
                pipeline_run_id VARCHAR NOT NULL,
                invoice_id VARCHAR NOT NULL,
                account_id VARCHAR NOT NULL,
                invoice_date DATE NOT NULL,
                amount DECIMAL(18, 2) NOT NULL,
                currency VARCHAR NOT NULL,
                payment_status VARCHAR NOT NULL,
                days_past_due INTEGER NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL,
                PRIMARY KEY (pipeline_run_id, invoice_id)
            );

            CREATE TABLE IF NOT EXISTS curated_support_cases (
                pipeline_run_id VARCHAR NOT NULL,
                case_id VARCHAR NOT NULL,
                account_id VARCHAR NOT NULL,
                opened_at TIMESTAMPTZ NOT NULL,
                closed_at TIMESTAMPTZ,
                severity VARCHAR NOT NULL,
                category VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                sla_breached BOOLEAN NOT NULL,
                resolution_minutes INTEGER,
                updated_at TIMESTAMPTZ NOT NULL,
                PRIMARY KEY (pipeline_run_id, case_id)
            );

            CREATE TABLE IF NOT EXISTS curated_product_usage_daily (
                pipeline_run_id VARCHAR NOT NULL,
                account_id VARCHAR NOT NULL,
                usage_date DATE NOT NULL,
                product VARCHAR NOT NULL,
                active_users INTEGER NOT NULL,
                api_calls BIGINT NOT NULL,
                failed_requests BIGINT NOT NULL,
                availability_pct DECIMAL(5, 2) NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL,
                PRIMARY KEY (pipeline_run_id, account_id, usage_date, product)
            );

            CREATE OR REPLACE VIEW serving_account_health AS
            WITH invoice_metrics AS (
                SELECT
                    pipeline_run_id,
                    account_id,
                    count(*) FILTER (WHERE payment_status = 'OVERDUE') AS overdue_invoices,
                    coalesce(
                        sum(amount) FILTER (WHERE payment_status = 'OVERDUE'), 0
                    ) AS overdue_amount,
                    max(updated_at) AS updated_at
                FROM curated_invoices
                GROUP BY pipeline_run_id, account_id
            ),
            support_metrics AS (
                SELECT
                    pipeline_run_id,
                    account_id,
                    count(*) FILTER (
                        WHERE status = 'OPEN' AND severity = 'SEV1'
                    ) AS open_sev1_cases,
                    count(*) FILTER (WHERE sla_breached) AS sla_breaches,
                    max(updated_at) AS updated_at
                FROM curated_support_cases
                GROUP BY pipeline_run_id, account_id
            ),
            usage_metrics AS (
                SELECT
                    pipeline_run_id,
                    account_id,
                    sum(api_calls) AS api_calls,
                    sum(failed_requests) AS failed_requests,
                    round(avg(availability_pct), 2) AS availability_pct,
                    max(updated_at) AS updated_at
                FROM curated_product_usage_daily
                GROUP BY pipeline_run_id, account_id
            )
            SELECT
                r.business_date,
                a.pipeline_run_id,
                a.account_id,
                a.account_name,
                a.segment,
                a.region,
                a.industry,
                a.account_status,
                a.annual_contract_value,
                a.owner_team,
                coalesce(i.overdue_invoices, 0) AS overdue_invoices,
                coalesce(i.overdue_amount, 0) AS overdue_amount,
                coalesce(s.open_sev1_cases, 0) AS open_sev1_cases,
                coalesce(s.sla_breaches, 0) AS sla_breaches,
                coalesce(u.api_calls, 0) AS api_calls,
                coalesce(u.failed_requests, 0) AS failed_requests,
                u.availability_pct,
                greatest(a.updated_at, i.updated_at, s.updated_at, u.updated_at) AS data_updated_at
            FROM curated_accounts AS a
            JOIN pipeline_runs AS r USING (pipeline_run_id)
            LEFT JOIN invoice_metrics AS i USING (pipeline_run_id, account_id)
            LEFT JOIN support_metrics AS s USING (pipeline_run_id, account_id)
            LEFT JOIN usage_metrics AS u USING (pipeline_run_id, account_id);

            CREATE OR REPLACE VIEW analytics_freshness AS
            SELECT
                max(business_date) AS latest_business_date,
                max(data_updated_at) AS data_updated_at,
                count(DISTINCT pipeline_run_id) AS published_runs,
                count(*) AS serving_rows
            FROM serving_account_health;
            """
        )

    def publish(self, result: StructuredBatchResult) -> PublicationResult:
        fingerprint = _batch_fingerprint(result)
        existing = self.connection.execute(
            "SELECT batch_fingerprint FROM pipeline_runs WHERE pipeline_run_id = ?",
            [result.pipeline_run_id],
        ).fetchone()
        if existing:
            if existing[0] != fingerprint:
                raise ReplayConflict(
                    "pipeline_run_id already exists with a different accepted-data fingerprint"
                )
            return PublicationResult(result.pipeline_run_id, inserted_rows=0, replayed=True)

        tables = {
            "curated_accounts": (
                "account_id",
                "account_name",
                "segment",
                "region",
                "industry",
                "account_status",
                "annual_contract_value",
                "contract_id",
                "owner_team",
                "updated_at",
            ),
            "curated_invoices": (
                "invoice_id",
                "account_id",
                "invoice_date",
                "amount",
                "currency",
                "payment_status",
                "days_past_due",
                "updated_at",
            ),
            "curated_support_cases": (
                "case_id",
                "account_id",
                "opened_at",
                "closed_at",
                "severity",
                "category",
                "status",
                "sla_breached",
                "resolution_minutes",
                "updated_at",
            ),
            "curated_product_usage_daily": (
                "account_id",
                "usage_date",
                "product",
                "active_users",
                "api_calls",
                "failed_requests",
                "availability_pct",
                "updated_at",
            ),
        }

        inserted_rows = 0
        self.connection.execute("BEGIN TRANSACTION")
        try:
            self.connection.execute(
                "INSERT INTO pipeline_runs VALUES (?, ?, ?, ?, current_timestamp)",
                [
                    result.pipeline_run_id,
                    result.business_date,
                    result.batch_id,
                    fingerprint,
                ],
            )
            for entity, rows in result.accepted.items():
                table = f"curated_{entity}"
                columns = tables[table]
                values = [
                    (result.pipeline_run_id, *(row[column] for column in columns)) for row in rows
                ]
                if values:
                    placeholders = ", ".join("?" for _ in range(len(columns) + 1))
                    self.connection.executemany(
                        f"INSERT INTO {table} VALUES ({placeholders})", values
                    )
                    inserted_rows += len(values)
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise
        return PublicationResult(result.pipeline_run_id, inserted_rows, replayed=False)

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> LocalAnalyticsStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
