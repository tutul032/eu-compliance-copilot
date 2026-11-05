# eu-compliance-copilot

Bilingual RAG + agent copilot for GDPR, AI Act and DORA on Databricks

## Problem

Compliance analysts, DPOs and ML engineers have to answer questions about GDPR, the EU AI Act and
DORA every week, in German and English. Today they search EUR-Lex by hand and lose time finding
the exact article; generic chatbots answer fluently but cite wrong or outdated articles.

The copilot answers in German or English with article-level citations from the official texts,
says clearly when a question is outside its corpus, refuses to give legal advice, and can query
operational data through tools. Details: [docs/discovery/discovery.md](docs/discovery/discovery.md).

## Status

Sprint 0 (scaffold), version 0.0.1. Nothing is ingested, indexed or answered yet; the modules in
`src/eucc/` are stubs. What works: CI (ruff, pytest) and the validator for the evaluation question
set ([eval/questions.yaml](eval/questions.yaml)). Decisions: [docs/adr/](docs/adr/).

## Development

```bash
uv sync
uv run ruff check .
uv run python -m pytest
```

### Pending cleanup

The smoke test behind [ADR 0002](docs/adr/0002-embedding-model.md) left two objects in dev that
nothing uses: the index `workspace.compliance_dev.vs_smoke_idx` (on endpoint `eucc-vs`) and its
source table `workspace.compliance_dev.vs_smoke`. Delete them by hand, the index first, once
`chunks_idx` is built. Not yet done: nothing in this repo deletes them.

```bash
databricks vector-search-indexes delete-index workspace.compliance_dev.vs_smoke_idx -p free
databricks tables delete workspace.compliance_dev.vs_smoke -p free
```

## Data sources and licence

Legal texts: © European Union, https://eur-lex.europa.eu/, 1998-2026. Reused under Commission
Decision 2011/833/EU.

Only the Official Journal of the EU is authentic. This project gives no legal advice.

Runs on Databricks Free Edition (non-commercial use).

## Licence

See [LICENSE](LICENSE).
