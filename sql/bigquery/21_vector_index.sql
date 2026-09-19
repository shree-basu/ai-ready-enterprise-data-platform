-- SQLFluff 3.3.1 does not yet parse BigQuery CREATE VECTOR INDEX.
-- This file is contract-tested against the current GoogleSQL syntax instead.
-- Index creation is asynchronous and can create BigQuery storage/compute cost if executed.
CREATE VECTOR INDEX document_chunk_embedding_idx
ON `project_id.enterprise_knowledge.document_chunks` (embedding)
STORING (
    document_id,
    document_version,
    chunk_text,
    classification,
    allowed_groups,
    account_id,
    effective_from,
    effective_to,
    source_uri,
    is_active,
    document_updated_at,
    embedding_version
)
OPTIONS (
    index_type = 'IVF',
    distance_type = 'COSINE',
    ivf_options = '{"num_lists":100}'
);
