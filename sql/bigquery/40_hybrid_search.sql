-- Stable hybrid reference: reciprocal-rank fusion avoids the Preview single-query API.
-- Parameters match 30_vector_search.sql and add search_query and fusion_constant.
WITH semantic AS (
    SELECT
        base.chunk_id,
        base.document_id,
        base.chunk_text,
        base.source_uri,
        distance,
        ROW_NUMBER() OVER (ORDER BY distance, base.chunk_id) AS semantic_rank
    FROM VECTOR_SEARCH(
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
),

lexical AS (
    SELECT
        chunk_id,
        document_id,
        chunk_text,
        source_uri,
        ROW_NUMBER() OVER (
            ORDER BY CHAR_LENGTH(chunk_text), chunk_id
        ) AS lexical_rank
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
        AND SEARCH(chunk_text, @search_query, analyzer => 'LOG_ANALYZER')
    QUALIFY lexical_rank <= @candidate_k
)

SELECT
    COALESCE(semantic.chunk_id, lexical.chunk_id) AS chunk_id,
    COALESCE(semantic.document_id, lexical.document_id) AS document_id,
    COALESCE(semantic.chunk_text, lexical.chunk_text) AS chunk_text,
    COALESCE(semantic.source_uri, lexical.source_uri) AS source_uri,
    semantic.distance,
    semantic.semantic_rank,
    lexical.lexical_rank,
    COALESCE(1.0 / (@fusion_constant + semantic.semantic_rank), 0.0)
    + COALESCE(1.0 / (@fusion_constant + lexical.lexical_rank), 0.0) AS fused_score
FROM semantic
FULL OUTER JOIN lexical USING (chunk_id)
ORDER BY fused_score DESC, chunk_id
LIMIT @candidate_k;
