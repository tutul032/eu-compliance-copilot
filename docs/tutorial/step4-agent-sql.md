# Step 4 — Agent with regulation search and a guarded SQL tool

Project 3 · EU Compliance Copilot · Tutorial 05

> Goes to: `docs/tutorial/step4-agent-sql.md` · Decision: `docs/adr/0006-agent-design.md`

## Goal

One assistant that answers law questions, data questions and mixed questions:

- "What was the average DE-LU day-ahead price yesterday?" → SQL on Project 1's tables
- "Our day-ahead load forecast supports grid operations. Could it be high-risk under the AI Act, and what is its current error?" → AI Act citation **and** the live forecast error

**Result:** route 1.000, data 1.000, behavior 1.000, citation hit 0.912, key facts 0.847.

## Concepts

**Explicit router instead of free tool-calling.** A LangGraph workflow: the router (LLM, JSON) decides `needs_regulation` / `needs_data`, fixed nodes run the tools, one answer node writes the response. The routes are logged and measurable, and behavior is stable between runs.

**Query rewriting.** Users say "load forecast" and "grid"; the law says "safety components in the management and operation of … supply of electricity". The router rewrites the question into legal terms (`regulation_query`), and retrieval searches with both versions.

**A guarded SQL tool.** The LLM writes SQL, but only against a whitelisted catalog. A validator (`sqlglot`) allows exactly one SELECT on catalog tables and enforces a LIMIT. Read-only by construction.

**Data citations.** Data rows are cited as `[D1]`, separately from law citations `[n]`. A data row never counts as a legal source.

**Graded against SQL, not against text.** Each tool question has a `reference_sql`; the grader compares the numbers in the answer with the reference result.

## Step 1 — Branch, tables, warehouse

```powershell
git switch master
git pull
git switch -c step4/agent-sql
databricks tables list workspace energy_prod -p free
databricks warehouses list -p free
```

**Actual (relevant part):**

```
workspace.energy_prod.gold_dq_anomalies               MANAGED
workspace.energy_prod.gold_dq_daily_summary           MANAGED
workspace.energy_prod.gold_dq_daily_summary_text      MANAGED
workspace.energy_prod.gold_energy_daily               MATERIALIZED_VIEW
workspace.energy_prod.gold_energy_hourly              MATERIALIZED_VIEW
workspace.energy_prod.gold_forecast_accuracy          MANAGED
workspace.energy_prod.gold_forecast_hourly            MANAGED
...
ID                Name                          Size      State
4228ff3a910b0b5e  Serverless Starter Warehouse  2X-Small  STOPPED
```

The price lives in `gold_energy_daily.price_avg_eur_mwh`. A search for "price" in table names found nothing — list everything and read the columns. A stopped serverless warehouse starts on the first query.

## Step 2 — Build the agent (Claude Code)

> Read `src/eucc/rag.py`, `docs/adr/0005-generation-configuration.md`, `eval/questions.yaml`.
> 1. **Energy catalog** `src/eucc/tools/energy_catalog.yaml` from `databricks tables get` for `gold_energy_hourly`, `gold_energy_daily`, `gold_forecast_accuracy`, `gold_forecast_hourly`, `gold_dq_daily_summary`, `gold_dq_anomalies`, `gold_dq_daily_summary_text`: columns with types, a one-line meaning, example questions. For error/accuracy questions: select `mape`, `mae`, `baseline_mape`.
> 2. **`src/eucc/tools/sql.py`:** `generate_sql` (gpt-oss, JSON, catalog + today's date in the prompt), `validate_sql` (sqlglot: exactly one SELECT, only catalog tables fully qualified, no DDL/DML, `LIMIT 200`), `run_sql` via `statement_execution.execute_statement` on warehouse `4228ff3a910b0b5e` (configurable); one retry with the error message.
> 3. **`src/eucc/agent.py` (LangGraph):** router node (JSON `needs_regulation`, `needs_data`, `regulation_query` — legal terms only, no article numbers unless the user gave them; keyword fallback), `search_regulation` (original question + `regulation_query`, merged), `query_energy_data`, `answer` (ADR-0005 contract, data cited `[D1]`). MLflow tracing on every node.
> 4. CLI `python -m eucc.ask --agent "…"` showing route, regulation query, SQL, answer, citations and data rows.
> 5. `reference_sql` for tool-01–04; job `eval_agent` with `route_correct`, `data_correct` (any-of reference values), and the `eval_rag` metrics.
> 6. Unit tests: validator (DROP, two statements, foreign table, missing LIMIT, allowed CTE), router fallback, graph routes.

## Step 3 — First answers

```powershell
$env:DATABRICKS_CONFIG_PROFILE = "free"
uv run python -m eucc.ask --agent "What was the average DE-LU day-ahead price yesterday?"
```

**Actual:**

```
Route: data (llm)
SQL: SELECT ROUND(price_avg_eur_mwh, 2) AS avg_price_eur_mwh FROM workspace.energy_prod.gold_energy_daily
     WHERE date_local = CAST('2026-11-08' AS DATE) LIMIT 200
Status: answered
The average DE‑LU day‑ahead price for 2026‑11‑08 was 242.13 EUR/MWh [D1].
146189 ms
```

146 s is the warehouse starting from STOPPED; later calls take seconds.

**Verify the number yourself** (SQL editor, or the API with a JSON file):

```powershell
$body = '{"warehouse_id":"4228ff3a910b0b5e","statement":"SELECT date_local, price_avg_eur_mwh FROM workspace.energy_prod.gold_energy_daily ORDER BY date_local DESC LIMIT 3","wait_timeout":"30s"}'
[IO.File]::WriteAllText("$env:TEMP\q.json", $body)
databricks api post /api/2.0/sql/statements -p free --json "@$env:TEMP\q.json"
```

**Actual:** 8 Nov = 242.13 — matches.

The mixed question, first version:

```powershell
uv run python -m eucc.ask --agent "Our day-ahead load forecast supports grid operations. Could it be high-risk under the AI Act, and what is its current error?"
```

**Actual:** route `regulation + data`, MAPE 2.36 % correct — but the law part cited **Art. 43** (conformity assessment) as if it defined high-risk. Annex III was not retrieved: the question's words don't match the law's words.

## Step 4 — Query rewriting

Add `regulation_query` to the router (see Step 2, item 3) and search with both queries.

**Actual after the change:**

```
Route: regulation + data (llm)
Regulation query: AI system used as safety component in the management and operation of critical
infrastructure, supply of electricity, high-risk classification AI Act Article 6, Annex III
...
Under Annex III of the AI Act, AI systems that are intended to be used as safety components in the
management and operation of critical ... electricity supply are classified as high-risk AI systems [5].
... mean absolute percentage error of 2.36 % [D1].
  [5] AIA Annex III — High-risk AI systems referred to in Article 6(2)
      https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:02024R1689-20260727#anx_III
```

Annex III with a working anchor. The rewritten query here still contained article numbers — fixed in Step 5.

## Step 5 — Evaluate and fix

```powershell
databricks bundle deploy -t dev -p free
databricks bundle run eval_agent -t dev -p free
```

| Run | route | data | behavior | citation hit | key facts | p95 |
|---|---|---|---|---|---|---|
| rag_medium (step 3) | — | — | 1.000 | 0.882 | 0.829 | 5.5 s |
| agent, first | 1.000 | 0.750 | 1.000 | 0.941 | 0.840 | 9.96 s |
| **agent, final** | **1.000** | **1.000** | **1.000** | **0.912** | **0.847** | 12.2 s |

What the per-question CSV showed after the first run:

- **tool-04 `data_correct = False`:** the agent reported MAE (1,355.99 MWh), the reference expected MAPE (2.36 %). Both are "the current error" → the reference returns both and the grader accepts either; the catalog tells the agent to select MAPE, MAE and the baseline.
- **`regulation_query` invented article numbers** ("AI Act Article 61, 62, 63", "DORA Article 27") → rule: legal terms only, numbers only if the user gave them.
- **aia-02:** the list of prohibited practices broke off — the 800-word cap cut the long Art. 5. Improved in the final run (key facts 0.50 → 0.83).

After the final run: the slowest answers were the **first** questions of the run (pure law questions, 12–13 s) → cold endpoints, not the design. Warm-up before timing is added in step 6.

## Step 6 — Decision and PR

Record `docs/adr/0006-agent-design.md`, then:

```powershell
git branch --show-current
git add -A
git status --short
git commit -m "feat(agent): LangGraph router with query rewriting, guarded SQL tool, eval_agent; ADR-0006"
git push -u origin step4/agent-sql
gh pr create --fill --base master
gh pr checks --watch
gh pr merge --squash --delete-branch
git switch master
git pull
```

## Checkpoint

- [x] Router with logged routes and legal query rewriting
- [x] SQL tool: whitelist catalog, sqlglot validator, statement execution
- [x] Answers with law citations `[n]` and data citations `[D1]`
- [x] `eval_agent`: route 1.000, data 1.000, behavior 1.000, citation hit 0.912, key facts 0.847
- [x] One number verified end to end (242.13 EUR/MWh)

## Troubleshooting (real problems from this step)

| Symptom | Cause | Fix |
| --- | --- | --- |
| No price table found with `Select-String price` | The price is a column (`price_avg_eur_mwh`) in `gold_energy_daily` | List all tables, read columns with `tables get` |
| First data question takes 130–150 s | Serverless warehouse starts from STOPPED, auto-stops after idle | Expected; warm up before demos and eval timing |
| `accepts 1 arg(s), received 11` on `databricks api post --json '{…}'` | Windows PowerShell 5.1 splits inline JSON at spaces | Write the body with `[IO.File]::WriteAllText`, pass `--json @file` |
| Law answer cites Art. 43 as the high-risk rule | Annex III not retrieved: user words ≠ legal words | Query rewriting (`regulation_query`) |
| `data_correct = False` with a correct number | Ambiguous "error": MAE vs MAPE | Reference returns both, grader accepts either; catalog selects both |
| Rewritten query contains invented article numbers | Rewriting prompt allowed guessing | Legal terms only; numbers only from the user |
| List of prohibited practices cut off | 800-word cap on a long article | Known trade-off of ADR-0005; watch key facts per question |
| p95 above 10 s on regulation-only questions at the start of the run | Cold model endpoint | Warm-up call before timing (step 6) |

## What you learned

- Building an agent as an explicit LangGraph workflow, and why that beats free tool-calling here.
- Query rewriting to bridge user vocabulary and legal vocabulary.
- Letting an LLM write SQL safely: whitelist, validator, read-only execution.
- Grading data answers against reference SQL — and spotting when the question, not the agent, is ambiguous.
- Verifying a demo number end to end before showing it.

## Tested on

2026-11-09 · Windows 11 · Windows PowerShell 5.1 · Databricks Free Edition · `databricks-gpt-oss-120b` · LangGraph · sqlglot · SQL Statement Execution API (Serverless Starter Warehouse) · MLflow
