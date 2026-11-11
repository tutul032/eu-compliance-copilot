# v1.0.0 — EU Compliance Copilot

First release: bilingual (EN/DE) assistant for GDPR, AI Act (incl. Digital Omnibus 2026/1744) and DORA on Databricks, with article-level citations and a guarded SQL tool on the energy platform's data.

## Highlights

- 12 official sources from the Publications Office (Cellar), sha256-locked; 3,704 citable chunks
- Hybrid Vector Search; LangGraph agent with legal query rewriting and a validated read-only SQL tool
- Databricks App (Streamlit, DE/EN) running as its own service principal
- Terraform-managed identities and least-privilege grants; staging built entirely by CI
- Release evaluation gate on staging

## Evaluation

- Agent, dev, 40 questions: route 1.000, data 1.000, behavior 1.000, citation hit 0.912, key facts 0.847
- Release gate, staging, 36 questions: safety 1.000, answer rate 0.969, citation hit 0.879, key facts 0.778, errors 0, p95 8.8 s

## Known limitations

See `docs/model-card.md` (risk log R2–R5): credit-scoring obligations, classification conditions, long-article truncation, over-cautious guardrail.

Not legal advice.
