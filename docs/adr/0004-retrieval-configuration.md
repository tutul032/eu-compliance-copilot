# ADR-0004 — Retrieval configuration for citations

Status: Accepted (2026-11-05)

## Context

The RAG step must cite articles and annexes. Retrieval is measured with recall@k on the 34 questions of `eval/questions.yaml` that have expected sources (tool-only, out-of-scope and refusal items excluded). Index: `workspace.compliance_dev.chunks_idx`, 3,704 chunks (EN + DE), Delta Sync on `gold_chunks`, embedding `databricks-qwen3-embedding-0-6b`, HYBRID query.

## Evidence (MLflow experiment `eucc_retrieval_eval_dev`, 2026-11-05)

| Variant | recall@5 | recall@10 | EN @5 | DE @5 | cross @5 |
|---|---|---|---|---|---|
| no filter | 0.794 | 0.882 | 0.793 | 0.800 | 0.000 |
| filter `lang` | 0.794 | 0.853 | 0.793 | 0.800 | 0.000 |
| filter `lang` + `unit_type IN (article, annex)` | **0.882** | **0.912** | 0.862 | **1.000** | 0.667 |

Observations:
- Recitals are long and explanatory; they matched questions semantically and took up to 3 of 5 slots (e.g. dora-01, cross-02).
- The language filter alone does not help once aia-04 is scored correctly; differences of one question (0.03) are within noise for 34 questions.
- Remaining misses: aia-04 (application dates → Art. 113), aia-10 (penalties → Art. 5 retrieved instead of Art. 99), one cross-regulation question, tool-04 (load forecast → general-purpose AI recitals/articles).

## Decision

- Citation retrieval: HYBRID, `filters={"lang": <question language>, "unit_type": ["article", "annex"]}`, `num_results=10`.
- Recitals stay in the index for later "why" questions, but are not used as citation sources.
- Multi-regulation questions (cross-*) are addressed in the RAG step by one retrieval per regulation named in the question.

## Consequences

- Baseline to beat in later steps: recall@5 0.882, recall@10 0.912.
- German subset meets the ADR-0002 target (5/5), but the sample is small; re-check when the question set grows.
- Eval data bug found and fixed: a wildcard source (`OMNIBUS_AI Art. any`) counted aia-04 as a hit; wildcards are now rejected by validation.
