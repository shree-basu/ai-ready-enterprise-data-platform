-- Current-state freshness view. Consumers should fail closed on NULL or stale timestamps.
CREATE OR REPLACE VIEW `project_id.enterprise_audit.source_freshness` AS
SELECT
    analytics.latest_business_date,
    analytics.analytics_updated_at,
    documents.documents_updated_at,
    TIMESTAMP_DIFF(
        documents.documents_updated_at,
        analytics.analytics_updated_at,
        SECOND
    ) AS document_analytics_skew_seconds
FROM (
    SELECT
        MAX(business_date) AS latest_business_date,
        MAX(updated_at) AS analytics_updated_at
    FROM `project_id.enterprise_curated.accounts`
    WHERE business_date >= DATE_SUB(CURRENT_DATE('UTC'), INTERVAL 7 DAY)
) AS analytics
CROSS JOIN (
    SELECT MAX(document_updated_at) AS documents_updated_at
    FROM `project_id.enterprise_knowledge.document_chunks`
    WHERE DATE(document_updated_at) >= DATE_SUB(CURRENT_DATE('UTC'), INTERVAL 7 DAY)
) AS documents;

-- Operational audit reads must always include the partition predicate.
SELECT
    query_id,
    started_at,
    policy_decision,
    referenced_objects,
    rows_returned,
    bytes_processed,
    status,
    error_code
FROM `project_id.enterprise_audit.query_audit`
WHERE DATE(started_at) BETWEEN @audit_start_date AND @audit_end_date
ORDER BY started_at DESC
LIMIT @audit_row_limit;
