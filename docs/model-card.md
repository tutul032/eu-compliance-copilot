# Model card — EU Compliance Copilot v1.0.0

## Intended use

Orientation for compliance analysts, data-protection officers, ML engineers and ICT risk managers: find the relevant provisions of GDPR, the AI Act and DORA quickly, in English or German, with links to the official text; relate them to operational data of the energy platform.

**Not intended for:** legal advice, decisions about individual cases, national law, delegated/implementing acts, case law. The answer footer and the system prompt state this; legal-advice requests are refused.

## System

| Component | Choice |
|---|---|
| Chat model | `databricks-gpt-oss-120b` (Apache-2.0), temperature 0, reasoning effort medium |
| Embeddings | `databricks-qwen3-embedding-0-6b` (multilingual) |
| Retrieval | Databricks Vector Search, hybrid, filters language + article/annex |
| Orchestration | LangGraph: router (JSON + keyword fallback), regulation search, SQL tool, answer node |
| Guardrails | answer only from sources; JSON status contract; citation verification; answer without valid citation → `unsupported`; SQL whitelist + single-SELECT validator; read-only data access |

## Data

| Source | Version | Use |
|---|---|---|
| GDPR 02016R0679 | consolidated 04.05.2016 (EN rev 000.002, DE 000.003) | articles |
| GDPR 32016R0679 | OJ L 119, 4.5.2016 | recitals |
| AI Act 02024R1689 | consolidated 27.07.2026 incl. Reg. 2026/1744 (EN rev 001.001, DE 001.002) | articles, annexes I–XIV |
| AI Act 32024R1689 | OJ L 2024/1689 | recitals |
| DORA 32022R2554 | OJ L 333, 27.12.2022 (no consolidated version) | articles, recitals |
| Digital Omnibus on AI 32026R1744 | OJ L 2026/1744 | amendments, recitals |
| Energy platform | `workspace.energy_prod` (7 whitelisted tables) | prices, forecast accuracy, data quality |

Downloaded from the Publications Office (Cellar); integrity pinned by sha256 lock file. Only the Official Journal is legally authentic.

## Evaluation

40 questions (EN 31, DE 9) with expected articles, key facts and expected behavior. Results: see README. Release gate on staging (36 questions, data tool off): safety 1.000, answer rate 0.969, citation hit 0.879, key facts 0.778, errors 0, p95 8.8 s.

Key-fact coverage is scored by an LLM judge and varies by up to 0.10 between runs of the same system.

## Risk log

| # | Risk | Evidence | Mitigation / status |
|---|---|---|---|
| R1 | Confidently wrong legal content when the decisive paragraph is missing | dora-05 first answered "TLPT once a year" (correct: every three years) | Small-to-big context — fixed; regression covered by the question set |
| R2 | Wrong obligations for credit scoring | cross-02 applies chatbot transparency (Art. 50) / GPAI rules instead of Annex III 5(b) | Open — retrieval/rewriting improvement in backlog; documented limitation |
| R3 | High-risk classification stated without conditions | aia-03, tool-04: "is high-risk" without the safety-component condition and Art. 6(3) exceptions | Open — prompt rule for classification answers in backlog |
| R4 | Long articles cut by the 800-word context cap | aia-02: list of prohibited practices incomplete in one run | Partly mitigated (matched-paragraph centering); monitored via key facts |
| R5 | Over-cautious guardrail | gdpr-06 returned `unsupported` in a release run (answer without citation markers) | Accepted: safe failure; counted in answer-rate gate (≥ 0.95) |
| R6 | Ungrounded article references | R2 run cited an article that was not retrieved | Guardrail `unsupported`; citations verified against retrieved chunks |
| R7 | SQL misuse | LLM-written SQL | Whitelist, sqlglot single-SELECT validator, LIMIT, read-only grants |
| R8 | Ambiguous data questions | "current error": MAE vs MAPE | Catalog hint: report MAPE, MAE and baseline |
| R9 | Outdated law | Consolidation lags amendments; per-language corrigenda | Manifest with version + revision per language; lock file detects source changes |
| R10 | Latency / availability | Cold endpoints and warehouse: first answer up to 2 min | Warm-up on app start and before evaluation timing |

## Owner and contact

Tutul — portfolio project, non-commercial (Databricks Free Edition).
