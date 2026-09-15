-- Filter each partitioned source to the requested business date before aggregation.
CREATE OR REPLACE VIEW `project_id.enterprise_serving.account_health` AS
WITH invoice_metrics AS (
    SELECT
        pipeline_run_id,
        account_id,
        COUNTIF(payment_status = 'OVERDUE') AS overdue_invoices,
        SUM(IF(payment_status = 'OVERDUE', amount, 0)) AS overdue_amount,
        MAX(updated_at) AS updated_at
    FROM `project_id.enterprise_curated.invoices`
    WHERE business_date = CURRENT_DATE('UTC')
    GROUP BY pipeline_run_id, account_id
),

support_metrics AS (
    SELECT
        pipeline_run_id,
        account_id,
        COUNTIF(status = 'OPEN' AND severity = 'SEV1') AS open_sev1_cases,
        COUNTIF(sla_breached) AS sla_breaches,
        MAX(updated_at) AS updated_at
    FROM `project_id.enterprise_curated.support_cases`
    WHERE business_date = CURRENT_DATE('UTC')
    GROUP BY pipeline_run_id, account_id
),

usage_metrics AS (
    SELECT
        pipeline_run_id,
        account_id,
        SUM(api_calls) AS api_calls,
        SUM(failed_requests) AS failed_requests,
        ROUND(AVG(availability_pct), 2) AS availability_pct,
        MAX(updated_at) AS updated_at
    FROM `project_id.enterprise_curated.product_usage_daily`
    WHERE business_date = CURRENT_DATE('UTC')
    GROUP BY pipeline_run_id, account_id
)

SELECT
    account.business_date,
    account.pipeline_run_id,
    account.account_id,
    account.account_name,
    account.segment,
    account.region,
    account.industry,
    account.account_status,
    account.annual_contract_value,
    account.owner_team,
    COALESCE(invoice.overdue_invoices, 0) AS overdue_invoices,
    COALESCE(invoice.overdue_amount, 0) AS overdue_amount,
    COALESCE(support.open_sev1_cases, 0) AS open_sev1_cases,
    COALESCE(support.sla_breaches, 0) AS sla_breaches,
    COALESCE(usage.api_calls, 0) AS api_calls,
    COALESCE(usage.failed_requests, 0) AS failed_requests,
    usage.availability_pct,
    GREATEST(
        account.updated_at,
        invoice.updated_at,
        support.updated_at,
        usage.updated_at
    ) AS data_updated_at
FROM `project_id.enterprise_curated.accounts` AS account
LEFT JOIN invoice_metrics AS invoice USING (pipeline_run_id, account_id)
LEFT JOIN support_metrics AS support USING (pipeline_run_id, account_id)
LEFT JOIN usage_metrics AS usage USING (pipeline_run_id, account_id)
WHERE account.business_date = CURRENT_DATE('UTC');

-- This sanitized view directly reads the protected source dataset for authorization.
-- Authorization is a dataset access-control operation, not a SQL property of the view.
CREATE OR REPLACE VIEW `project_id.enterprise_serving.account_directory_shared` AS
SELECT
    business_date,
    account_id,
    segment,
    region,
    industry,
    account_status,
    updated_at AS data_updated_at
FROM `project_id.enterprise_curated.accounts`
WHERE business_date = CURRENT_DATE('UTC');
