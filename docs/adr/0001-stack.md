# 1. Stack: Databricks Free Edition, bundles, Terraform, hand-built RAG + agent

- **Status:** Proposed
- **Date:** 2026-11-01

## Context
The copilot answers GDPR, AI Act and DORA questions in German and English with article-level
citations, and queries operational data through tools (docs/discovery/discovery.md). It runs on
Databricks Free Edition: one Vector Search endpoint with one unit and no Direct Vector Access,
restricted outbound internet from serverless, one Databricks App that stops after 24 h, no Agent
Bricks, and non-commercial use only. Project 1 (energy-lakehouse-de) runs on the same workspace
and already has a working pattern for bundles, CI and Terraform.

Design decisions are recorded as ADRs in `docs/adr/` in Michael Nygard's format
(`NNNN-short-title.md` with **Status, Context, Decision, Consequences**), as in Project 1. ADRs
are immutable once accepted; a change of mind is a new ADR that supersedes the old one.

## Decision
- **Python package `eucc`** (src layout, uv, `uv.lock` committed): pydantic and pyyaml at
  runtime; pytest and ruff for development. Modules: `ingest`, `chunk`, `index`, `retrieve`,
  `agent`, `mcp_server`, `eval`.
- **Databricks Asset Bundle** (`databricks.yml`, bundle `eu-compliance-copilot`) with targets
  `dev`, `staging` and `prod` on the one Free Edition workspace, mapped to the schemas
  `workspace.compliance_dev`, `workspace.compliance_staging` and `workspace.compliance_prod`.
- **Terraform** (`infra/terraform/`) creates the three schemas; the bundle never does. Separate
  state from Project 1; no service principals or grants yet.
- **Ingestion outside Databricks:** EUR-Lex texts are downloaded in GitHub Actions or a
  container (serverless has restricted outbound internet) and uploaded to a UC volume, as in
  Project 1. Texts are not committed; only manifests (CELEX, language, version date, checksum).
- **Retrieval:** Vector Search Delta Sync index over a chunk table with Change Data Feed, one
  index per environment on the shared endpoint. Embedding model: ADR 0002.
- **Generation and orchestration:** `gpt-oss-120b` as default chat model, `llama-3-3-70b` as
  comparison in eval; LangGraph with MLflow tracing; tools exposed through an MCP server.
- **Evaluation:** the question set `eval/questions.yaml` is validated by `eucc.eval.questions`
  in every CI run; the eval itself runs on a sample in PR CI and on the full set nightly.
- **CI:** GitHub Actions `ci.yml` (job `ci`: `uv sync`, `ruff check`, `pytest`), same runner and
  action versions as Project 1. No Databricks auth in CI yet.

## Consequences
- Same tooling and layout as Project 1, so its runbook and dev container apply almost unchanged.
- RAG and agent are built by hand, which costs more code than Agent Bricks but is portable to
  the Azure sprint (AI Search + Azure OpenAI) behind the same eval.
- One endpoint and quotas are shared by all environments; a heavy eval run can affect dev.
- Free Edition is non-commercial only; the project stays a non-commercial portfolio.
