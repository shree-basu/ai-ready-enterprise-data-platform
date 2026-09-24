from __future__ import annotations

import duckdb
import pytest

from enterprise_platform.sql_policy import (
    ReadOnlySQLPolicy,
    SQLPolicyViolation,
    execute_governed_query,
)


@pytest.fixture
def connection():
    value = duckdb.connect(":memory:")
    value.execute("CREATE VIEW serving_account_health AS SELECT * FROM range(5) t(account_id)")
    try:
        yield value
    finally:
        value.close()


@pytest.fixture
def policy() -> ReadOnlySQLPolicy:
    return ReadOnlySQLPolicy({"serving_account_health", "analytics_freshness"})


def test_select_and_cte_over_allowlisted_view_are_accepted(policy: ReadOnlySQLPolicy) -> None:
    direct = policy.validate("SELECT account_id FROM serving_account_health")
    cte = policy.validate(
        "WITH risky AS (SELECT * FROM serving_account_health) SELECT account_id FROM risky"
    )

    assert direct.referenced_relations == frozenset({"serving_account_health"})
    assert cte.referenced_relations == frozenset({"serving_account_health"})


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO serving_account_health VALUES (1)",
        "DROP VIEW serving_account_health",
        "SELECT * FROM serving_account_health; SELECT 1",
        "SELECT * FROM serving_account_health -- bypass",
        "SELECT * FROM main.serving_account_health",
        "SELECT * FROM read_csv_auto('private.csv')",
        "SELECT * FROM 'private.csv'",
        "SELECT * FROM range(1000000000)",
        "SELECT getenv('SECRET')",
        "PRAGMA version",
    ],
)
def test_mutating_ambiguous_and_external_sql_is_rejected(
    policy: ReadOnlySQLPolicy, sql: str
) -> None:
    with pytest.raises(SQLPolicyViolation):
        policy.validate(sql)


def test_non_allowlisted_relation_is_rejected(policy: ReadOnlySQLPolicy) -> None:
    with pytest.raises(SQLPolicyViolation, match="outside the allowlist: private_accounts"):
        policy.validate("SELECT * FROM private_accounts")


def test_execution_is_parameterized_bounded_and_reproducible(
    connection: duckdb.DuckDBPyConnection, policy: ReadOnlySQLPolicy
) -> None:
    sql = "SELECT account_id FROM serving_account_health WHERE account_id >= ? ORDER BY account_id"
    first = execute_governed_query(connection, policy, sql, [1], max_rows=2)
    second = execute_governed_query(connection, policy, sql, [1], max_rows=2)

    assert first.columns == ("account_id",)
    assert first.rows == ((1,), (2,))
    assert first.truncated is True
    assert first.query_id == second.query_id


def test_execution_rejects_invalid_limits(
    connection: duckdb.DuckDBPyConnection, policy: ReadOnlySQLPolicy
) -> None:
    with pytest.raises(ValueError, match="max_rows must be positive"):
        execute_governed_query(connection, policy, "SELECT 1", max_rows=0)
