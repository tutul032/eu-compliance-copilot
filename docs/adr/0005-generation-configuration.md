# ADR-0005 — Grounded generation configuration

Status: Accepted (2026-11-07)

## Context

The copilot answers with `gpt-oss-120b` from sources retrieved per ADR-0004. Answers must be grounded (only from sources, with article citations), in the question's language, and must refuse legal advice and prompt injection and say when a topic is outside the corpus. Evaluated with `eval_rag` on 37 questions (all except pure tool questions).

## Evidence (MLflow experiment `eucc_rag_eval_dev`, 2026-11-07)

| Run | behavior | citation hit | key facts | p95 latency | max context |
|---|---|---|---|---|---|
| R1: 8 retrieved paragraphs | 1.000 | 0.824 | 0.734 | 3.8 s | — |
| R2: + full articles (small-to-big), `【n】` parsing | 0.973 | 0.853 | 0.827 | 17.3 s | ~23k tokens |
| **R3 medium: + 800-word cap per article, retry, grounding guardrail** | **1.000** | **0.882** | **0.829** | **5.5 s** | **4.8k tokens** |
| R3 low reasoning effort | 0.946 | 0.882 | 0.784 | 3.3 s | 4.8k tokens |

## Decision

- Output contract: JSON `{status: answered|out_of_scope|refused, answer}`; citations as `[n]` (also `【n】`) mapped to retrieved chunks; invalid numbers dropped.
- Context: small-to-big — the 3 top-ranked articles expanded around the matched paragraph(s), max ~800 words each; split articles (`-pN`) only matched part ± 1 neighbour; max 12 source blocks, ~6,000 words total.
- Grounding guardrail: `answered` without a valid citation becomes `unsupported` with a fixed message.
- Robustness: `max_tokens=1500`, one retry on JSON parse failure.
- `reasoning_effort = medium`, temperature 0.

## Consequences

- Targets met: behavior 100 %, key facts ≥ 80 %, p95 < 10 s. Citation hit 0.882 (target 0.90): the 4 misses are retrieval misses (aia-04 cites the Omnibus instead of AI Act Art. 113; cross-02 and cross-03 miss the relevant AI Act provisions; tool-04 needs the SQL tool and Annex III).
- Known risks for the risk log: R1 gave a confidently wrong TLPT interval (dora-05, fixed by full-article context); tool-04 named the wrong Annex III point when Annex III was not retrieved.
- Low reasoning effort is faster but lost two behavior answers — not worth 2 s.
