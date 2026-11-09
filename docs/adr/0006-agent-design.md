# ADR-0006 — Agent design: explicit router, query rewriting, guarded SQL tool

Status: Accepted (2026-11-09)

## Context

Questions combine EU law and operational data from Project 1 (prices, load-forecast accuracy, data-quality checks), e.g. "Our day-ahead load forecast supports grid operations. Could it be high-risk under the AI Act, and what is its current error?". The agent must pick the right sources, never write to data, and keep the guarantees of ADR-0005 (grounding, citations, refusals). Model: `gpt-oss-120b` on Databricks Free Edition.

## Options

1. Free tool-calling (the model decides tool calls in a loop).
2. **Explicit LangGraph workflow:** an LLM router returns JSON (`needs_regulation`, `needs_data`, `regulation_query`), then fixed nodes run the tools, then one answer node.

## Decision

Option 2.

- **Router:** gpt-oss JSON with a keyword fallback. `regulation_query` rewrites the question into legal terminology, without article numbers unless the user gave them. `search_regulation` searches with the original question **and** `regulation_query`, then merges.
- **SQL tool:** gpt-oss writes SQL from a whitelisted catalog (`src/eucc/tools/energy_catalog.yaml`, 7 tables in `workspace.energy_prod`). `sqlglot` validator: exactly one SELECT, only catalog tables, no DDL/DML, `LIMIT 200`. One retry with the error message. Executed through the SQL Statement Execution API on the serverless warehouse. Error/accuracy questions select MAPE, MAE and baseline MAPE.
- **Answer node:** the ADR-0005 contract; data rows cited as `[D1]`; data never counts as a law citation.

Why: the routes are explicit, testable and logged (`route_correct` is measurable); behavior is stable between runs; tool-calling with Free Edition models was the identified risk. The trade-off is less flexibility for multi-step questions.

## Evidence (`eval_agent`, 40 questions, 2026-11-09)

| Run | route | data | behavior | citation hit | key facts | p95 |
|---|---|---|---|---|---|---|
| rag_medium (ADR-0005, no agent) | — | — | 1.000 | 0.882 | 0.829 | 5.5 s |
| agent, first run | 1.000 | 0.750 | 1.000 | 0.941 | 0.840 | 9.96 s |
| **agent, final** (MAE+MAPE grading, no invented article numbers) | **1.000** | **1.000** | **1.000** | **0.912** | **0.847** | 12.2 s |

Data correctness checked end to end: 8 Nov 2026 average DE-LU price 242.13 EUR/MWh (agent = direct SQL).

## Consequences

- Query rewriting is the main gain over plain RAG (vocabulary mismatch, e.g. "load forecast / grid" → "safety component … supply of electricity", Annex III).
- p95 in the final run is driven by cold endpoints at the start of the run (the slowest answers are the first questions, with no SQL). The eval gets a warm-up call before timing (step 6). The serverless warehouse auto-stops; the first data question after idle takes 1–2 minutes → warm up the warehouse at app start (step 5).
- Risk log: cross-02 (credit scoring) answers with the wrong obligations (Art. 50/53 instead of Annex III 5(b)); classification answers state "is high-risk" without the safety-component condition and the Art. 6(3) exceptions (aia-03, tool-04).
- Read-only by construction: whitelist + validator + a warehouse identity with SELECT rights only (staging/prod identity grants in step 6).
