from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SQL_ROOT = ROOT / "sql" / "bigquery"


def _sql(name: str) -> str:
    return (SQL_ROOT / name).read_text(encoding="utf-8")


def test_reference_package_is_complete_and_intentionally_non_deployable() -> None:
    expected = {
        "00_datasets_and_tables.sql",
        "10_serving_views.sql",
        "20_search_index.sql",
        "21_vector_index.sql",
        "30_vector_search.sql",
        "40_hybrid_search.sql",
        "50_freshness_and_audit.sql",
    }

    assert {path.name for path in SQL_ROOT.glob("*.sql")} == expected
    for path in SQL_ROOT.glob("*.sql"):
        text = path.read_text(encoding="utf-8")
        assert "project_id." in text
        assert "shree-basu" not in text.casefold()
        assert not re.search(r"\b(?:gcloud|bq|terraform)\b", text, re.IGNORECASE)


def test_curated_tables_match_local_analytics_entities_and_enforce_partition_filters() -> None:
    ddl = _sql("00_datasets_and_tables.sql")

    for table in (
        "pipeline_runs",
        "accounts",
        "invoices",
        "support_cases",
        "product_usage_daily",
        "document_chunks",
        "query_audit",
    ):
        assert f".{table}`" in ddl
    assert ddl.count("require_partition_filter = TRUE") == 7
    assert "embedding ARRAY<FLOAT64> NOT NULL" in ddl
    assert "allowed_groups ARRAY<STRING> NOT NULL" in ddl


def test_vector_and_hybrid_queries_preserve_governance_before_ranking() -> None:
    index = _sql("21_vector_index.sql")
    vector = _sql("30_vector_search.sql")
    hybrid = _sql("40_hybrid_search.sql")

    assert "CREATE VECTOR INDEX document_chunk_embedding_idx" in index
    assert "distance_type = 'COSINE'" in index
    assert "ivf_options = '{\"num_lists\":100}'" in index
    for query in (vector, hybrid):
        assert "VECTOR_SEARCH(" in query
        assert "@minimum_document_date" in query
        assert "@principal_groups" in query
        assert "@principal_account_id" in query
        assert "embedding_version = @embedding_version" in query
    assert "SEARCH(chunk_text, @search_query" in hybrid
    assert "FULL OUTER JOIN lexical USING (chunk_id)" in hybrid
    assert "fused_score" in hybrid
    assert "Preview" in hybrid


def test_authorized_view_and_cost_boundaries_are_not_overclaimed() -> None:
    views = _sql("10_serving_views.sql")
    readme = (SQL_ROOT / "README.md").read_text(encoding="utf-8")

    assert "account_directory_shared" in views
    assert "FROM `project_id.enterprise_curated.accounts`" in views
    assert "Authorization is a dataset access-control operation" in views
    assert "SQL view creation alone does\nnot create that authorization" in readme
    assert "can incur charges" in readme


def test_freshness_and_audit_queries_include_bounded_partition_predicates() -> None:
    sql = _sql("50_freshness_and_audit.sql")

    assert sql.count("DATE_SUB(CURRENT_DATE('UTC'), INTERVAL 7 DAY)") == 2
    assert "BETWEEN @audit_start_date AND @audit_end_date" in sql
    assert "LIMIT @audit_row_limit" in sql
