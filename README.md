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

## Source and attribution

<!-- TODO: copy the exact attribution line from the EUR-Lex legal notice
     (docs/discovery/discovery.md, section 5). -->
Regulation texts: EUR-Lex, © European Union. _Attribution wording to be added from the EUR-Lex
legal notice._

Only the Official Journal is legally authentic; consolidated versions are documentation only.
**This project does not give legal advice.**

## Non-commercial use

This is a non-commercial portfolio project. It runs on Databricks Free Edition, which permits
non-commercial use only.

## Licence

See [LICENSE](LICENSE).
