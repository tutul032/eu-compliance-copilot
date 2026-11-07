# Step 3 — Grounded answers with article citations (RAG)

Project 3 · EU Compliance Copilot · Tutorial 04

> Goes to: `docs/tutorial/step3-rag-citations.md` · Decision: `docs/adr/0005-generation-configuration.md`

## Goal

- Answer questions in English or German **only from the retrieved law texts**, with citations that link to EUR-Lex.
- Say clearly when a question is outside the corpus, refuse legal advice and prompt injection.
- Measure it on the question set in MLflow and improve it by evidence, not guesswork.

**Result:** behavior correct 1.000, citation hit 0.882, key-fact coverage 0.829, p95 latency 5.5 s.

## Concepts

**Grounded generation.** The model receives numbered sources and must answer only from them, citing `[n]`. Code maps each `[n]` back to a chunk (article, paragraph, link) and drops numbers that don't exist — the model cannot invent a citation.

**A status field makes safety measurable.** The model returns `{status, answer}`. The eval compares `status` with the question's `expected_behavior` (answered / out_of_scope / refused) — no fuzzy text matching.

**Per-regulation retrieval.** If a question names two or more regulations (GDPR/DSGVO, AI Act/KI-VO, DORA), one extra search per regulation ensures each is represented in the context.

**Small-to-big.** Search on paragraphs (precise), give the model the surrounding article (complete). Fixes answers where the right article was found but the decisive paragraph was not.

**Grounding guardrail.** An answer with no valid citation is not shown as an answer: it becomes `unsupported`.

**Three metrics, one table.** Citation hit (an expected article is cited), behavior correctness, key-fact coverage (LLM judge checks the question's `key_facts`), plus latency and context size. Every change is a new MLflow run.

## Step 1 — Branch and endpoint names

```powershell
cd C:\dev\eu-compliance-copilot
git switch master
git pull
git switch -c step3/rag-citations
databricks serving-endpoints list -p free | Select-String "gpt-oss|qwen3-embedding"
```

**Expected:** the exact endpoint names (e.g. `databricks-gpt-oss-120b`).

## Step 2 — RAG module, CLI and eval job (Claude Code)

Final version of the prompt (after the improvement rounds below):

> Read `docs/adr/0004-retrieval-configuration.md`, `src/eucc/eval/retrieval.py`, `src/eucc/index.py`, `eval/questions.yaml`.
> 1. **`src/eucc/rag.py`** with `databricks-sdk` only (works locally with profile `free` and in jobs):
>    - `retrieve`: `vector_search_indexes.query_index` on `chunks_idx`, HYBRID, filters lang + `unit_type IN (article, annex)`, k=10; if ≥ 2 regulations are named (EN/DE keywords), one extra query per regulation (k=5); merge, dedupe, keep each named regulation.
>    - **Small-to-big:** expand the 3 top-ranked articles around the matched paragraph(s), max ~800 words each; for split articles (`-pN`) only the matched part ± 1 neighbour; max 12 source blocks, ~6,000 words total.
>    - `generate`: `serving_endpoints.get_open_ai_client()`, the gpt-oss endpoint, temperature 0, `max_tokens=1500`, `reasoning_effort` parameter (default medium). System prompt: answer only from numbered sources, cite `[n]`, answer in the question's language, corpus scope, JSON `{"status": "answered"|"out_of_scope"|"refused", "answer": "..."}`. gpt-oss may return `content` as a list of parts → take text parts, parse the first JSON object; on failure retry once with "Return only the JSON object.", then status `error`.
>    - `answer`: language detection, retrieve, generate, citation check (`[n]`, `【n】`, `[n][m]`, `[n, m]`; drop invalid numbers); **guardrail**: `answered` without valid citations → `unsupported`. Citation label = the chunk's localized header; URL with `#art_<n>` / `#rct_<n>` / annex anchor. `@mlflow.trace` on all three.
> 2. CLI `python -m eucc.ask "question"`.
> 3. `src/eucc/eval/answers.py` + job `eval_rag`: all questions except `tool`; metrics `citation_hit`, `behavior_correct`, `key_fact_coverage` (LLM judge), `unsupported`, `errors`, p50/p95 latency, `max_tokens_in_context`; runs `rag_medium` and `rag_low`; per-question CSV with `retrieved_chunk_ids` and `cited_chunk_ids`.
> 4. Unit tests without network for detection, merge, expansion, caps, citation parsing, guardrail, JSON parsing, metrics.

## Step 3 — First answers (CLI)

```powershell
$env:DATABRICKS_CONFIG_PROFILE = "free"
uv run python -m eucc.ask "How quickly must a personal data breach be notified to the supervisory authority?"
uv run python -m eucc.ask "Innerhalb welcher Frist muss eine Datenpanne gemeldet werden?"
uv run python -m eucc.ask "What does the NIS2 Directive require from energy companies?"
```

**Actual:**

```
Status: answered
A personal data breach must be reported to the supervisory authority without undue delay and, where
feasible, no later than 72 hours after the controller becomes aware of it. ... [1]
  [1] GDPR Art. 33(1) — Notification of a personal data breach to the supervisory authority

Status: answered
Der Verantwortliche muss eine Datenpanne unverzüglich und möglichst binnen 72 Stunden ... [8]
  [8] ... Art. 33(1) — Meldung von Verletzungen des Schutzes personenbezogener Daten an die Aufsichtsbehörde
      https://eur-lex.europa.eu/legal-content/DE/TXT/HTML/?uri=CELEX:02016R0679-20160504

Status: out_of_scope
The provided sources do not cover the NIS2 Directive, so I cannot answer that question.
```

Locally each call took 11–13 s; inside Databricks the same calls take 2–4 s (network and client overhead from the laptop).

## Step 4 — Evaluate and improve in rounds

```powershell
databricks bundle deploy -t dev -p free
databricks bundle run eval_rag -t dev -p free
```

| Round | Change | behavior | citation hit | key facts | p95 |
|---|---|---|---|---|---|
| R1 | 8 retrieved paragraphs | 1.000 | 0.824 | 0.734 | 3.8 s |
| R2 | + full articles, `【n】` parsing, `retrieved_chunk_ids` in CSV | 0.973 | 0.853 | 0.827 | 17.3 s |
| R3 | + 800-word cap, split-part rule, retry, guardrail | **1.000** | **0.882** | **0.829** | **5.5 s** |
| R3 low | same, `reasoning_effort=low` | 0.946 | 0.882 | 0.784 | 3.3 s |

What the per-question CSV showed after each round:

**After R1:**

- gdpr-10: correct answer citing Art. 45 and 46, but `cited_chunk_ids` empty — gpt-oss wrote `【8】` instead of `[8]`. Parser bug.
- aia-10, dora-01, dora-05: right article retrieved, decisive paragraph missing. dora-05 answered **"mindestens einmal jährlich"** — confidently wrong (DORA Art. 26: every three years). Fix: small-to-big.

After the fix, the CLI check:

```powershell
uv run python -m eucc.ask "Wie oft muss ein bedrohungsorientierter Penetrationstest (TLPT) durchgeführt werden?"
```

**Actual:** "… mindestens alle drei Jahre durchführen. Die zuständige Aufsichtsbehörde kann die Häufigkeit jedoch … anpassen [1]" — `[1] DORA Art. 26`.

**After R2:** p95 17.3 s and one `error`. The slowest rows had 17–23k tokens: the Omnibus Art. 1 (a 10,000-word list of amendments, split into ~32 parts) was expanded completely. The long output then broke the JSON (aia-08). Also: cross-02 answered without any valid citation (named Art. 26(5), which was not retrieved) — an ungrounded claim. Fix: cap per article, split-part rule, retry, guardrail.

**After R3:** max context 4.8k tokens, no errors, p95 5.5 s. Medium kept; low reasoning loses two behavior answers.

## Step 5 — Record the decision

`docs/adr/0005-generation-configuration.md`: output contract, small-to-big with caps, guardrail, retry, `reasoning_effort=medium`; remaining misses and risks.

## Step 6 — Commit and PR

```powershell
git branch --show-current
git add -A
git status --short
git commit -m "feat(rag): grounded answers with citations, guardrail and eval_rag job; ADR-0005"
git push -u origin step3/rag-citations
gh pr create --fill --base master
gh pr checks --watch
gh pr merge --squash --delete-branch
git switch master
git pull
```

**Expected:** `git status` lists only intended files (no `data/`), `ci pass`, merged.

## Checkpoint

- [x] CLI answers in EN and DE with EUR-Lex citations; out-of-scope recognized
- [x] `eval_rag` with behavior, citation hit, key facts, latency, context size in MLflow
- [x] Behavior 1.000, key facts 0.829, p95 5.5 s; citation hit 0.882 with documented misses
- [x] ADR-0005

## Troubleshooting (real problems from this step)

| Symptom | Cause | Fix |
| --- | --- | --- |
| Correct answer but no citations recorded | gpt-oss used `【n】` brackets | Accept `[n]`, `【n】`, `[n][m]`, `[n, m]` |
| Confidently wrong interval (TLPT "once a year") | Decisive paragraph of DORA Art. 26 not in context | Small-to-big: expand top articles |
| p95 17 s, 23k-token prompts | Omnibus Art. 1 (~32 split parts) expanded completely | Cap per article; for `-pN` articles only matched part ± 1 |
| `status = error` | Very long output broke the JSON | `max_tokens`, one retry with "Return only the JSON object." |
| Answer names an article that was not retrieved | Ungrounded generation | Guardrail: no valid citation → `unsupported` |
| 11–13 s per answer locally | Network and client setup from the laptop | Measure latency inside Databricks (eval job) |
| Job output ends with a list of `Trace(...)` objects | Return value echoed | Don't return/print the traces |
| Citation hit (0.882) below retrieval recall@10 | Retrieved but not cited, or retrieval misses | Read `retrieved_chunk_ids` vs `cited_chunk_ids` per question |

## What you learned

- Building grounded RAG with a structured output contract and verified citations.
- Measuring behavior, citations and content separately, and improving one round at a time from per-question evidence.
- Small-to-big context, and why it needs size caps.
- The latency/quality trade-off of reasoning effort — decided by numbers.
- Guardrails against ungrounded answers, and recording residual risks honestly.

## Tested on

2026-11-07 · Windows 11 · Windows PowerShell 5.1 · Databricks Free Edition · `databricks-gpt-oss-120b` · Vector Search HYBRID · MLflow tracing and experiments
