# Sprint 1c — Vector Search index and retrieval evaluation

Project 3 · EU Compliance Copilot · Tutorial 03

> Goes to: `docs/tutorial/sprint1c-index-eval.md` · Covers issues #9 (S1-6) and #10 (S1-7) · Closes Sprint 1

## Goal

- A Delta Sync index on `gold_chunks`, created and synced by the bundle job, never by hand.
- A retrieval evaluation on the question set, logged to MLflow, that compares retrieval configurations with numbers.
- A decision record (ADR-0004) for the configuration the RAG step will use.

**Result in our run:** 3,704 / 3,704 chunks indexed; best configuration recall@5 = 0.882, recall@10 = 0.912, German 5/5.

## Concepts

**Delta Sync index.** The index follows a Delta table through its Change Data Feed. The job builds the table, then asks the index to sync. Because the build is an idempotent MERGE, a re-run causes no re-embedding.

**recall@k.** For each question: is at least one expected article among the top-k retrieved chunks? (For `all_required: true`, all of them.) It measures retrieval alone, before any LLM — if the right article isn't retrieved, no prompt can cite it.

**Variants, not guesses.** Each configuration is a separate MLflow run with the same questions. The per-question CSV shows *why* a question missed.

**Evaluate the evaluation.** A wrong expected source makes a miss look like a hit. Read the per-question results before trusting the aggregate.

## Step 1 — Branch

```powershell
git switch master
git pull
git switch -c s1-6/index-eval
```

## Step 2 — Index task and eval job (Claude Code)

> Read `resources/build_corpus.job.yml`, `src/eucc/jobs/build_corpus.py`, `eval/questions.yaml`, `src/eucc/eval/questions.py`, `docs/adr/0002-embedding-model.md`.
> 1. `src/eucc/jobs/index.py` + task `index` in `build_corpus` (depends on build; `databricks-vectorsearch` in the environment): create Delta Sync index `<catalog>.<schema>.chunks_idx` on endpoint `eucc-vs` if missing (source `gold_chunks`, primary key `chunk_id`, embedding source `chunk_text`, embedding endpoint `databricks-qwen3-embedding-0-6b`, TRIGGERED, sync columns chunk_id, reg, lang, unit_type, article, paragraph, heading, celex, version_date, source_url, chunk_text), else `sync()`; poll until ready and indexed rows = table rows (timeout 30 min).
> 2. `src/eucc/eval/retrieval.py` + job `eval_retrieval`: for every question with `expected_sources`, HYBRID query per variant; hit rules for articles and annexes, `all_required`; recall overall, per category, per language; one MLflow run per variant with a per-question CSV artifact; experiment path from a bundle variable.
> 3. Hit matching and aggregation as pure functions with unit tests.

## Step 3 — Build and index

```powershell
databricks bundle deploy -t dev -p free
databricks bundle run build_corpus -t dev -p free
```

**Actual:**

```
MERGE `workspace`.`compliance_dev`.`gold_chunks`: inserted=0 updated=0 deleted=0
Task index:
syncing workspace.compliance_dev.chunks_idx
index state=ONLINE_TRIGGERED_UPDATE indexed=3704/3704
index state=ONLINE_NO_PENDING_UPDATE indexed=3704/3704
```

The first sync of 3,704 chunks took about 3 minutes.

## Step 4 — First evaluation

```powershell
databricks bundle run eval_retrieval -t dev -p free
```

**First run:** `no_filter` recall@5 0.794, `lang_filter` 0.824; cross-regulation 0.000; tool-04 0.000.

Open the `lang_filter` run → **Artifacts** → per-question CSV, and read every row with `hit = False` — and the suspicious `True` rows:

```
aia-04  expected: AIA Art. 113 OR OMNIBUS_AI Art. any
        top-5:    … omnibus_ai-en-article-1-0-2026-07-24-p31   → True
```

Art. 113 is not in the top 5 — the hit came from a leftover wildcard source. **The real score was lower.** Also visible in the CSV: recitals take up to 3 of 5 slots (e.g. dora-01, cross-02).

## Step 5 — Fix the eval, add variants

Claude Code:

> (1) `aia-04` `expected_sources` → `[{reg: AIA, article: "113"}]`; reject `article: "any"` in validation. (2) Unit test: annex chunk `article="III"`, `unit_type="annex"` matches `{reg: AIA, annex: "III"}`. (3) Query with `num_results=10`, compute recall@5 and recall@10 from the same results; add variant `lang_articles` with `filters={"lang": <lang>, "unit_type": ["article", "annex"]}`.

```powershell
databricks bundle deploy -t dev -p free
databricks bundle run eval_retrieval -t dev -p free
```

**Actual:**

| Variant | recall@5 | recall@10 | DE @5 | cross @5 |
|---|---|---|---|---|
| no_filter | 0.794 | 0.882 | 0.800 | 0.000 |
| lang_filter | 0.794 | 0.853 | 0.800 | 0.000 |
| **lang_articles** | **0.882** | **0.912** | **1.000** | 0.667 |

Reading it:

- Excluding recitals from citation retrieval gains 3 questions at k=5 (gdpr-10, cross-03, one cross question).
- With aia-04 scored correctly, the language filter alone no longer helps; one question (0.03) is noise at n = 34.
- The 90 % target is met at k=10; German 5/5 (small sample).

## Step 6 — Record the decision

Add `docs/adr/0004-retrieval-configuration.md` (decision: HYBRID, filter `lang` + `unit_type IN (article, annex)`, k = 10; recitals not cited; one retrieval per regulation for cross questions in the RAG step) and update ADR-0002: German target met on 5/5, re-check later.

## Step 7 — Delete the Sprint 0 smoke index

The real index now exists; the smoke index occupies the same endpoint.

```powershell
databricks vector-search-indexes delete-index workspace.compliance_dev.vs_smoke_idx -p free
databricks tables delete workspace.compliance_dev.vs_smoke -p free
databricks vector-search-indexes list-indexes eucc-vs -p free
```

**Expected:** only `workspace.compliance_dev.chunks_idx` is listed.

## Step 8 — Commit, PR, close Sprint 1

```powershell
git branch --show-current
git add src tests resources eval docs corpus
git status --short
git commit -m "feat(retrieval): Delta Sync index in build job, recall@k evaluation, ADR-0004 (closes #9, closes #10)"
git push -u origin s1-6/index-eval
gh pr create --fill --base master
gh pr checks --watch
gh pr merge --squash --delete-branch
git switch master
git pull
gh api -X PATCH repos/tutul032/eu-compliance-copilot/milestones/2 -f state=closed --jq '[.title, .state] | @tsv'
```

**Expected:** `ci pass`, merged, `Sprint 1  closed`.

## Checkpoint

- [x] `chunks_idx` created and synced by the job; 3,704 / 3,704
- [x] Three retrieval variants as MLflow runs with per-question CSVs
- [x] Eval data bug fixed (wildcard source)
- [x] ADR-0004: lang + articles/annexes, k = 10; baseline 0.882 / 0.912
- [x] Smoke index deleted; Sprint 1 closed

## Troubleshooting (real problems from this step)

| Symptom | Cause | Fix |
| --- | --- | --- |
| aia-04 counted as hit although Art. 113 is not retrieved | Leftover expected source `OMNIBUS_AI Art. any` acted as a wildcard | Fix the question; reject wildcards in validation |
| cross-02 and tool-04 at 0 — suspected annex-matching bug | No annex chunk was in the top 5 at all (real misses) | Read the per-question CSV before guessing; add a unit test for annex matching |
| Recitals fill the top-k | Long explanatory text matches questions semantically | Filter `unit_type` to article/annex for citation retrieval |
| Small differences between variants | 34 questions: one question = 0.03 | Treat ±1 question as noise; decide on clear gaps |

## What you learned

- Building and syncing a Vector Search index from a job, idempotently.
- Measuring retrieval with recall@k on a fixed question set, per category and language, in MLflow.
- Reading per-question results to find eval bugs and failure patterns.
- Turning measurements into a recorded decision (ADR) the next step builds on.

## Tested on

2026-11-05 · Windows 11 · Windows PowerShell 5.1 · Databricks Asset Bundles · Vector Search (STANDARD endpoint, Delta Sync, HYBRID) · MLflow · Databricks Free Edition (serverless)
