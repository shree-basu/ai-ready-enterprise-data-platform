# Data model and identity contracts

| Asset | Grain / durable identity |
|---|---|
| `accounts` | one account / `account_id` |
| `invoices` | one invoice / `invoice_id` |
| `support_cases` | one support case / `case_id` |
| `product_usage_daily` | one account, usage date, and product |
| `serving_account_health` | one pipeline run and account |
| knowledge document | one `document_id` and `document_version` |
| knowledge chunk | document identity, version, content hash, chunk index, and chunk-text hash |
| embedding | chunk ID plus provider, model, dimension, and embedding version |
| pipeline run | business date, batch ID, and source schema version |
| audit event | canonical event type/time/actor hash/action/decision/object/details payload |
| operation request | operation type, target run, requester, reason, and created time |

Structured duplicates resolve by the latest `updated_at`, then a canonical record hash. Input
ordering therefore cannot change the winner. Invalid records and orphan references are quarantined,
and every entity must satisfy `source = accepted + quarantined`.

Publishing to DuckDB is transactional. An identical pipeline-run replay inserts nothing; a changed
payload under the same run identity fails closed. Document replays keep a single version, while a
same-version content mutation is quarantined. A new effective version deactivates old chunks without
deleting their historical identity.

The local embedding model is deterministic token hashing for tests only. Its provider/model/
dimension/version lineage prevents incompatible vectors from entering one index. A production
embedding change is a reindex event, not an in-place reinterpretation of existing vectors.
