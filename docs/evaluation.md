# Offline evaluation contract

`evaluation/datasets/enterprise_agent_v1.json` is explicitly versioned. Every case binds a question,
principal/groups/region, account scope, expected tools, structured outcome, relevant documents and
chunks, citations, access outcome, and freshness requirement.

Retrieval evaluation uses the same positive cases for semantic-only, lexical-only, and hybrid modes.
It computes macro Recall@K, mean reciprocal rank, nDCG@K, and empty-result rate. No cases are removed
because a mode performs poorly.

Agent checks cover exact tool sequence, argument-controlled tool behavior, citation presence and
existence, authorization leakage, deterministic cited-text phrase support, stale-data disclosure,
restricted refusal, and the `PENDING_APPROVAL` operation boundary. The highest-scoring inaccessible
document is filtered before ranking in dedicated regression tests.

These are deterministic software and retrieval-contract evaluations. They do not measure natural
language helpfulness, semantic entailment, hosted-model quality, adversarial robustness, production
recall, or human satisfaction. No external model/judge is called.

Run `python -m evaluation.offline` to regenerate
`evaluation/reports/baseline_v1.json` from the deterministic `unauthorized-document` scenario. The
committed report includes all three retrieval modes and the mandatory restricted-access refusal; it
is local evidence only and does not claim production performance.
