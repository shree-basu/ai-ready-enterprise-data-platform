CREATE SEARCH INDEX document_chunk_text_idx
ON `project_id.enterprise_knowledge.document_chunks` (chunk_text)
OPTIONS (analyzer = 'LOG_ANALYZER');
