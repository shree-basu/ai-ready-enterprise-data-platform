-- STATIC REFERENCE ONLY: project_id is intentionally non-deployable until replaced.
CREATE SCHEMA IF NOT EXISTS `project_id.enterprise_curated`
OPTIONS (
    location = 'us-central1',
    description = 'Curated governed enterprise analytics'
);

CREATE SCHEMA IF NOT EXISTS `project_id.enterprise_knowledge`
OPTIONS (
    location = 'us-central1',
    description = 'Governed document chunks and embeddings'
);

CREATE SCHEMA IF NOT EXISTS `project_id.enterprise_serving`
OPTIONS (
    location = 'us-central1',
    description = 'Consumer-facing authorized views'
);

CREATE SCHEMA IF NOT EXISTS `project_id.enterprise_audit`
OPTIONS (
    location = 'us-central1',
    description = 'Pipeline, query, and freshness evidence'
);

CREATE TABLE IF NOT EXISTS `project_id.enterprise_curated.pipeline_runs` (
    pipeline_run_id STRING NOT NULL,
    business_date DATE NOT NULL,
    batch_id STRING NOT NULL,
    batch_fingerprint STRING NOT NULL,
    published_at TIMESTAMP NOT NULL
)
PARTITION BY business_date
CLUSTER BY pipeline_run_id
OPTIONS (require_partition_filter = TRUE);

CREATE TABLE IF NOT EXISTS `project_id.enterprise_curated.accounts` (
    pipeline_run_id STRING NOT NULL,
    business_date DATE NOT NULL,
    account_id STRING NOT NULL,
    account_name STRING NOT NULL,
    segment STRING NOT NULL,
    region STRING NOT NULL,
    industry STRING NOT NULL,
    account_status STRING NOT NULL,
    annual_contract_value NUMERIC NOT NULL,
    contract_id STRING NOT NULL,
    owner_team STRING NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    structured_row_id STRING NOT NULL,
    record_hash STRING NOT NULL
)
PARTITION BY business_date
CLUSTER BY region, segment, account_id
OPTIONS (require_partition_filter = TRUE);

CREATE TABLE IF NOT EXISTS `project_id.enterprise_curated.invoices` (
    pipeline_run_id STRING NOT NULL,
    business_date DATE NOT NULL,
    invoice_id STRING NOT NULL,
    account_id STRING NOT NULL,
    invoice_date DATE NOT NULL,
    amount NUMERIC NOT NULL,
    currency STRING NOT NULL,
    payment_status STRING NOT NULL,
    days_past_due INT64 NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    structured_row_id STRING NOT NULL,
    record_hash STRING NOT NULL
)
PARTITION BY business_date
CLUSTER BY account_id, payment_status
OPTIONS (require_partition_filter = TRUE);

CREATE TABLE IF NOT EXISTS `project_id.enterprise_curated.support_cases` (
    pipeline_run_id STRING NOT NULL,
    business_date DATE NOT NULL,
    case_id STRING NOT NULL,
    account_id STRING NOT NULL,
    opened_at TIMESTAMP NOT NULL,
    closed_at TIMESTAMP,
    severity STRING NOT NULL,
    category STRING NOT NULL,
    status STRING NOT NULL,
    sla_breached BOOL NOT NULL,
    resolution_minutes INT64,
    updated_at TIMESTAMP NOT NULL,
    structured_row_id STRING NOT NULL,
    record_hash STRING NOT NULL
)
PARTITION BY business_date
CLUSTER BY account_id, status, severity
OPTIONS (require_partition_filter = TRUE);

CREATE TABLE IF NOT EXISTS `project_id.enterprise_curated.product_usage_daily` (
    pipeline_run_id STRING NOT NULL,
    business_date DATE NOT NULL,
    account_id STRING NOT NULL,
    usage_date DATE NOT NULL,
    product STRING NOT NULL,
    active_users INT64 NOT NULL,
    api_calls INT64 NOT NULL,
    failed_requests INT64 NOT NULL,
    availability_pct NUMERIC NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    structured_row_id STRING NOT NULL,
    record_hash STRING NOT NULL
)
PARTITION BY business_date
CLUSTER BY account_id, product
OPTIONS (require_partition_filter = TRUE);

CREATE TABLE IF NOT EXISTS `project_id.enterprise_knowledge.document_chunks` (
    chunk_id STRING NOT NULL,
    document_id STRING NOT NULL,
    document_version INT64 NOT NULL,
    chunk_index INT64 NOT NULL,
    chunk_text STRING NOT NULL,
    chunk_text_hash STRING NOT NULL,
    content_hash STRING NOT NULL,
    classification STRING NOT NULL,
    allowed_groups ARRAY<STRING> NOT NULL,
    account_id STRING,
    effective_from DATE NOT NULL,
    effective_to DATE,
    source_uri STRING NOT NULL,
    owner STRING NOT NULL,
    title STRING NOT NULL,
    is_active BOOL NOT NULL,
    chunk_version STRING NOT NULL,
    document_updated_at TIMESTAMP NOT NULL,
    embedding_id STRING NOT NULL,
    embedding ARRAY<FLOAT64> NOT NULL,
    embedding_provider STRING NOT NULL,
    embedding_model STRING NOT NULL,
    embedding_dimension INT64 NOT NULL,
    embedding_version STRING NOT NULL,
    embedded_at TIMESTAMP NOT NULL
)
PARTITION BY DATE(document_updated_at)
CLUSTER BY classification, account_id, document_id
OPTIONS (require_partition_filter = TRUE);

CREATE TABLE IF NOT EXISTS `project_id.enterprise_audit.query_audit` (
    query_id STRING NOT NULL,
    principal_id_hash STRING NOT NULL,
    started_at TIMESTAMP NOT NULL,
    completed_at TIMESTAMP,
    policy_decision STRING NOT NULL,
    referenced_objects ARRAY<STRING> NOT NULL,
    rows_returned INT64,
    bytes_processed INT64,
    status STRING NOT NULL,
    error_code STRING
)
PARTITION BY DATE(started_at)
CLUSTER BY status, policy_decision
OPTIONS (
    require_partition_filter = TRUE,
    partition_expiration_days = 400
);
