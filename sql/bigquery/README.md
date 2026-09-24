# BigQuery production-reference SQL

These files are design evidence, not an executable deployment package. The deliberately invalid
`project_id` placeholder prevents accidental execution until an operator performs an explicit,
reviewed substitution in a separately controlled environment.

The repository has no cloud credentials, deployment workflow, Terraform, or BigQuery execution
script. Creating tables or indexes and running queries can incur charges, so none of this SQL is
executed by local tests or repository automation.

`account_directory_shared` becomes an authorized view only after a data owner grants that view access
to the source dataset using a separately reviewed IAM/API operation. SQL view creation alone does
not create that authorization. Consumers still require query-job permission in their execution
project and view-data permission on the serving dataset.

The IVF vector index is appropriate for small query batches. Index build/population is asynchronous,
and `VECTOR_SEARCH` can fall back to brute force while coverage is incomplete. The hybrid query uses
stable batch `VECTOR_SEARCH`, indexed `SEARCH`, and reciprocal-rank fusion; it intentionally avoids
the Preview single-query hybrid API.

SQLFluff 3.3.1 parses and lints the package except `21_vector_index.sql`, because that release does
not yet recognize BigQuery `CREATE VECTOR INDEX`. A focused test validates that file's required
GoogleSQL clauses instead; the exclusion is explicit in `.sqlfluffignore`.
