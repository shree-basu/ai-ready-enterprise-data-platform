# Local operations runbook

## Validate

Run formatting, lint, compilation, tests, BigQuery-dialect SQL lint, and the repository safety scan.
CI performs the same cloud-free checks on `ubuntu-latest` with read-only repository permission.

## Demonstrate

Run `python -m demo.run --scenario account-risk`. Expected evidence includes balanced Beam outputs,
21 accepted structured rows, five documents/chunks, four ADK tool calls, a real citation, fresh-source
status, a pending operation request, audit events, local metrics, and an empty cloud-operation list.

## Replay

Never edit an existing immutable source directory. Replaying unchanged inputs is safe: structured
publication, document versions, chunks, indexes, and audit IDs are deterministic. Conflicting bytes
under an existing durable identity fail closed. Corrections use a new batch or document version.

## Investigate

Start with reconciliation and quarantine reason codes, then audit decisions, freshness discrepancy,
and bounded metrics. Audit deliberately omits raw source content, document text, prompts, SQL, tokens,
and credentials. Local output is evidence for this reference dataset only.

## Security and cost response

If a workflow ever requests secrets, OIDC, manual dispatch, nonstandard runners, artifact upload, a
cloud CLI, infrastructure mutation, image publication, or an unpinned action, stop and remove it.
Run `python scripts/check_repo_safety.py` before accepting the change. This repository has no approved
deployment procedure; any real cloud experiment requires a separate repository/environment, billing
review, least-privilege design, and explicit human authorization.
