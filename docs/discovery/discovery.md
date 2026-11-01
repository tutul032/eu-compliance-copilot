# EU Compliance Copilot — Discovery

Status: draft for Sprint 0 · Date: 2026-11-01 · Owner: Tutul · Repo: `tutul032/eu-compliance-copilot`

## 1. Problem statement

Compliance analysts, DPOs and ML engineers at mid-size EU companies in **energy and financial services** must answer questions about **GDPR, the EU AI Act and DORA** every week ("Do we have to report this incident, to whom, by when?", "Is our forecasting model high-risk?"). Today they search EUR-Lex by hand, switch between German and English versions, and lose time finding the exact article. Generic chatbots answer fluently but cite wrong or outdated articles — a known failure mode that is unacceptable in compliance work.

**The copilot** answers questions in German or English with **article-level citations** from the official texts, says clearly when something is outside its corpus, refuses to give legal advice, and can query the company's own operational data (Project 1 energy tables) through tools so that obligations can be linked to real systems.

**One-sentence version (README / CV):** A bilingual RAG + agent copilot on Databricks that answers GDPR, AI Act and DORA questions with verified article citations, evaluated on a 40-question set in CI, exposed as an MCP server, and ported to Azure AI Search + Azure OpenAI.

## 2. Users and jobs to be done

| Persona | Job | Example |
| --- | --- | --- |
| Compliance analyst (energy utility, DE) | Find the obligation and the article fast, in German | "Innerhalb welcher Frist muss eine Datenpanne gemeldet werden?" |
| ML engineer | Classify own AI system under the AI Act | "Is a day-ahead load forecast for grid operation high-risk?" |
| ICT risk manager (bank) | Map an incident to GDPR + DORA duties | "Ransomware exposed customer data — what must we report?" |
| Ops lead | Link regulation to live system metrics | "What is the current load-forecast error and is the model documented?" |

## 3. Scope

**In scope (v1.0):** GDPR (32016R0679), AI Act (32024R1689) **as amended by the Digital Omnibus on AI, Reg. (EU) 2026/1744**, DORA (32022R2554); EN + DE; article + recital + annex granularity; Project 1 tables via a read-only SQL tool.

**Out of scope (v1.0):** legal advice; national law (BDSG, KWG); delegated/implementing acts and RTS (e.g. DORA incident-reporting timelines); NIS2, Data Act, CRA; case law. The copilot must say these are not covered rather than guess.

## 4. Success metrics (measured in CI per release)

| Metric | Target v1.0 | How measured |
| --- | --- | --- |
| Citation hit rate | ≥ 90 % | expected article ∈ cited articles, question set |
| Retrieval recall@5 | ≥ 90 % | expected article in top-5 chunks |
| Answer correctness | ≥ 80 % | key facts present (LLM judge + spot check) |
| Refusal / out-of-scope correctness | 100 % | adversarial + out-of-scope items |
| Cross-lingual hit rate (DE query) | ≥ 85 % | German subset |
| p95 latency | < 10 s | MLflow traces |
| Cost per answer | tracked, trend only | tokens × list price (Free Edition: tokens) |

## 5. Licence and reuse check

| Item | Terms | Consequence |
| --- | --- | --- |
| EUR-Lex texts (GDPR, AI Act, DORA, Omnibus) | Commission reuse policy, Decision 2011/833/EU: commercial and non-commercial reuse allowed, acknowledge source, don't distort meaning, no Commission liability | ✅ Reuse OK. Add attribution in README, UI footer and model card. Copy the exact attribution line from the EUR-Lex legal notice page (checkpoint below). |
| Authenticity | Only the Official Journal (eOJ) is legally authentic; consolidated versions are documentation only | Answer footer: "Not legal advice. Source: EUR-Lex, consolidated text as of <date>." Store `celex`, `version_date`, `source_url` per chunk. |
| Databricks Free Edition | Non-commercial use only | ✅ Portfolio is non-commercial. State it in README. |
| gpt-oss-120b | Apache-2.0 | ✅ |
| Llama 3.3 70B | Llama 3.3 Community Licence (attribution, AUP) | ✅ If used: "Built with Llama" in model card. |
| Qwen3.5 / Qwen3-Embedding | Apache-2.0 (verify on model card) | ✅ |
| gte-large-en / bge-large-en | Apache-2.0 / MIT (verify) | ✅ but English-only → not primary |
| Raw corpus in git | Allowed, but large and versioned elsewhere | Do **not** commit texts; ingest to a UC volume. Commit only manifests (CELEX, language, version date, checksum). |

Checkpoint: open the EUR-Lex legal notice, copy the attribution wording into `docs/discovery/licences.md`, screenshot it for the tutorial.

## 6. Platform constraints (Free Edition) and design consequences

| Constraint | Consequence |
| --- | --- |
| One Vector Search endpoint, one unit; no Direct Vector Access | **Delta Sync index only.** Chunks live in a Delta table with Change Data Feed. One endpoint shared by dev/staging/prod → one index per env on the same endpoint. |
| Restricted outbound internet from serverless | EUR-Lex download runs outside Databricks (GitHub Actions / container, same pattern as the Project 1 extractor) → upload to UC volume. |
| One Databricks App, stops after 24 h | Demo UI restarted before demos; UI is thin, logic stays in the agent package. |
| Agent Bricks / Knowledge Assistant not available | Build RAG + agent by hand (fine — that is the skill employers ask for). |
| Daily quotas | Eval runs on a sample in PR CI, full set nightly/on release. |

## 7. Key decisions (become ADRs in Sprint 0)

1. **Embedding model:** `qwen3-embedding-0-6b` (multilingual) — DE questions must find EN and DE chunks. Validate in Sprint 1 against the German subset; fallback: index DE and EN separately and route by query language.
2. **Chat model:** `gpt-oss-120b` default, `llama-3-3-70b` as comparison in eval (report both).
3. **Orchestration:** LangGraph (with MLflow tracing). Azure sprint: Azure AI Foundry Agent Service.
4. **Chunking:** one chunk per article paragraph (fallback: per article), with recital/annex as separate types; metadata `regulation, celex, lang, article, paragraph, version_date`.
5. **Versioned corpus:** AI Act ingested as consolidated text after Reg. 2026/1744; the omnibus itself indexed too so "what changed" questions are answerable.

## 8. Risks

| Risk | Mitigation |
| --- | --- |
| Hallucinated or wrong article numbers | Cite only retrieved chunk IDs; post-check that every cited article exists in retrieved set |
| Outdated law (amendments) | `version_date` per chunk, corpus manifest, scheduled check for new consolidated versions |
| Users treat output as legal advice | System prompt + UI disclaimer + refusal tests |
| Free Edition quota stops CI | Small PR eval sample; cache embeddings |
| German answer quality | German subset metrics reported separately |

## 9. Sprint plan (1-week sprints)

| Sprint | Goal | Demo |
| --- | --- | --- |
| 0 | Repo, board, CI, bundle, Terraform, discovery merged, VS endpoint | green CI, smoke index query |
| 1 | Ingestion + chunking (EUR-Lex → volume → bronze/silver chunks), Delta Sync index | hybrid query returns Art. 33 GDPR |
| 2 | RAG with citations + eval harness in CI | eval report in PR |
| 3 | LangGraph agent: find-article + SQL tools; MCP server | agent answers mixed question |
| 4 | LLMOps metrics, guardrails, red-teaming, Databricks App | UI demo + metrics dashboard |
| 5 | Azure sprint: AI Search + Azure OpenAI + Foundry agent, same eval | side-by-side eval |
| H | Hardening: model card, risk log, runbook, sign-off → v1.0.0 | release |
