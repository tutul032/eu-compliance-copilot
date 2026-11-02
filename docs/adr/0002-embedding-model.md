# 2. Embedding model: multilingual qwen3-embedding-0-6b

- **Status:** Accepted (2026-11-01), to be re-validated with German-subset recall@5 in Sprint 1 (S1-7).

## Context
Users ask in German or English, and the corpus holds both language versions of each regulation.
A German question must find the right article in either language (target: cross-lingual hit rate
≥ 85 % on the German subset, retrieval recall@5 ≥ 90 %; docs/discovery/discovery.md). The
English-only models available on Free Edition (`gte-large-en`, `bge-large-en`) would match a
German query only against German chunks, and only weakly.

## Decision
- Use qwen3-embedding-0-6b (multilingual) for all chunks.
- Index EN **and** DE texts in one index with a `lang` column, so German queries also get keyword matches.
- Fallback if DE recall@5 < 85 %: filter by query language, or use per-language indexes.

## Evidence (smoke test, 3 EN chunks, HYBRID, databricks-qwen3-embedding-0-6b)
| Query | Top-1 | Score | Runner-up | Score |
|---|---|---|---|---|
| EN "How fast must a data breach be reported?" | GDPR Art. 33 | 1.0 | GDPR Art. 6 | 0.976 |
| DE "Innerhalb welcher Frist muss eine Datenpanne gemeldet werden?" | GDPR Art. 33 | 0.5 | DORA Art. 64 | 0.492 |

The DE score of 0.5 is consistent with only the vector side contributing (no keyword overlap with English text); the margin is small.

## Consequences
First sync of a new index took ~<your minutes> on Free Edition; jobs must wait for `ready` before querying.
