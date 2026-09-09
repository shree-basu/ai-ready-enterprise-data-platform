# AI-Ready Enterprise Data Platform

A production-realistic reference implementation showing how one governed enterprise data
foundation can support structured analytics and grounded agent applications.

The business scenario is an **Enterprise Customer Operations Intelligence Platform** built
entirely from deterministic synthetic accounts, invoices, support cases, product usage, and
enterprise documents.

## Evidence boundary

| Category | Meaning |
|---|---|
| Implemented and locally tested | Executed without network access using local engines and fakes |
| Implemented and statically validated for GCP | Request, schema, and SQL contracts checked without authentication |
| Production architecture target—not deployed | Documented mapping to Dataflow, BigQuery, Vertex AI, and Google ADK runtimes |
| Not evidenced | Cloud deployment, performance, scale, SLA, cost, or production security enforcement |

This repository is intentionally incapable of deploying infrastructure. It contains no Terraform,
cloud-authentication workflow, deploy command, service-account key, paid model call, or GCP client
constructed by a default local path.

## Development status

The platform is being implemented in evidence-driven slices. Architecture, local execution,
governance, evaluation, and failure semantics will be documented alongside working code.

