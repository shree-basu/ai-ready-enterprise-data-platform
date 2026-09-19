# Failure detection and recovery

| Failure | Detection and behavior | Retry/replay and operator response |
|---|---|---|
| Missing marker/manifest, wrong path, checksum or count | batch validation fails before processing | correct or redeliver under a new immutable batch ID |
| Structured schema/domain error | row quarantine with stable identity; counts reconcile | correct source in a new batch; unchanged replay is a no-op |
| Duplicate key | deterministic winner; losers quarantined | inspect competing updates; replay is order-independent |
| Orphan account reference | dependent row quarantined | load/correct the parent in a new batch |
| Malformed document | document quarantine; no silent skip | correct JSON/envelope and issue a new immutable delivery |
| Same-version content mutation | fails closed as version mutation | increment document version; never overwrite knowledge identity |
| Stale document version | quarantined; active index unchanged | confirm source ordering and submit a valid later version |
| Embedding failure/count/dimension mismatch | chunk quarantine or index rejection | retry local provider; a model change requires explicit reindex |
| Vector-index conflict | atomic local add rejects changed active chunk | investigate identity/version contract before replay |
| Empty/restricted retrieval | explicit empty/refusal; inaccessible text never reaches model | refine authorized query or request access outside the model |
| Stale/missing/future/skewed sources | freshness tool returns discrepancy and answer warning | repair upstream publication; do not hide the warning |
| Invalid/unsafe SQL | parser policy blocks before execution and records audit/metric | use an approved bounded `SELECT` over allowlisted views |
| Local query/tool error | stable failure code; no raw SQL/prompt/content in audit | diagnose local fixture or contract, then safely retry |
| Invalid approval transition | state machine rejects requester self-approval or wrong state | obtain a distinct approver; no cloud executor exists |
