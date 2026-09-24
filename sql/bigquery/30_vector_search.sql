-- Expected parameters: query_embedding, minimum_document_date, principal_groups,
-- principal_account_id, embedding_version, candidate_k.
SELECT
    base.chunk_id,
    base.document_id,
    base.chunk_text,
    base.source_uri,
    base.classification,
    base.document_updated_at,
    distance
FROM
    VECTOR_SEARCH(
        (
            SELECT *
            FROM `project_id.enterprise_knowledge.document_chunks`
            WHERE
                DATE(document_updated_at) >= @minimum_document_date
                AND is_active
                AND embedding_version = @embedding_version
                AND (account_id IS NULL OR account_id = @principal_account_id)
                AND (
                    classification = 'PUBLIC'
                    OR EXISTS (
                        SELECT 1
                        FROM UNNEST(allowed_groups) AS allowed_group
                        WHERE allowed_group IN UNNEST(@principal_groups)
                    )
                )
        ),
        'embedding',
        (SELECT @query_embedding AS embedding),
        query_column_to_search => 'embedding',
        top_k => @candidate_k,
        distance_type => 'COSINE'
    )
ORDER BY distance, base.chunk_id;
