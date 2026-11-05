# Sprint 1b — Parse and chunk EU law into citable units

Project 3 · EU Compliance Copilot · Tutorial 02

> Goes to: `docs/tutorial/sprint1b-parse-chunk.md` · Covers issues #7 (S1-4) and #8 (S1-5)

## Goal

Turn the 12 raw XHTML files from tutorial 01 into:

- `silver_provisions` — one row per article paragraph, recital or annex, with clean text and metadata,
- `gold_chunks` — one citable chunk per provision, with a citation header, stable id and Change Data Feed,

built by a bundle job on Databricks serverless and protected by a data contract.

**Result in our run:** 3,704 chunks, identical locally and on Databricks.

## Concepts

**Provision vs chunk.** A *provision* is a legal unit (GDPR Art. 33(1), Recital 85, Annex III). A *chunk* is what the search index stores: the provision text plus a header. Long provisions are split into several chunks with overlap.

**Citation header inside the chunk.** Every chunk starts with its source, e.g. `GDPR Art. 33(1) — Notification of a personal data breach…`. The embedding then carries the article identity, and the LLM sees the source in every retrieved passage — the basis for answers with citations.

**Which unit from which file.** Consolidated texts are current but have no recitals; original acts have recitals but outdated articles (the AI Act original lacks the Omnibus changes). So: articles/annexes from consolidated texts, recitals from originals, both from DORA and the Omnibus. The manifest field `role` drives this.

**Stable ids and idempotent writes.** `chunk_id` = `<reg>-<lang>-<unit_type>-<article>-<paragraph|0>-<version_date>[-pN]`. The job MERGEs on it, so a re-run changes nothing — the Delta Sync index (step 2) then only syncs real changes.

**Data contract.** Counts per source (articles, recitals, annexes) must equal the manifest; no empty texts, no duplicate ids. In this tutorial the contract caught a wrong number in the manual spike — again.

## Prerequisites

- Tutorial 01 done: `data/raw/` filled locally and `/Volumes/workspace/compliance_dev/raw/eurlex` uploaded
- Branch rule: work on a feature branch; `gh` on Windows only

## Step 1 — Branch

```powershell
cd C:\dev\eu-compliance-copilot
git switch master
git pull
git switch -c s1-4/parse-chunk
```

## Step 2 — Parser, chunker and contract (Claude Code)

> In C:\dev\eu-compliance-copilot on branch `s1-4/parse-chunk`. Read `docs/spikes/s1-2-eurlex-format.md`, `corpus/manifest.yaml` and `src/eucc/corpus/manifest.py` first. Then **inspect the real files in `data/raw/`** (one consolidated and one original file per language) before writing the parser, especially how annexes and recitals are marked up.
>
> 1. **`src/eucc/parse.py`** (BeautifulSoup + lxml), two variants:
>    - *consolidated* (ELI classes): article = `div.eli-subdivision[id^="art_"]`, number from the id; title from `.stitle-article-norm`; paragraphs = direct `div.norm` children, number from `span.no-parag` (strip `&nbsp;` and dot); article without numbered paragraphs = one unit with paragraph null; annexes: find their container from the real files.
>    - *original OJ* (`oj-` classes): recitals by `id="rct_N"`; articles as in the file (needed for DORA and the Omnibus).
>    - Unit types per entry follow the manifest `role`.
>    - Strip block markers (`▼B`, `▼M1` …), inline spans (`►C1 … ◄`) and soft hyphens; collapse whitespace; drop deleted provisions (text only `__________`). Keep found markers as `amended_by` (list).
>    - Rows: `provision_id, reg, celex, lang, version_date, revision, unit_type (article|recital|annex), article (string), paragraph (string|null), heading, text, amended_by, source_url`.
> 2. **`src/eucc/chunk.py`:** one chunk per provision; `chunk_text` = header + "\n" + text. Header in the entry's language: EN `GDPR Art. 33(1) — <title>`, DE `DSGVO Art. 33 Abs. 1 — <title>` (AI Act = KI-VO, Omnibus = KI-Omnibus), recitals `GDPR Recital 85` / `DSGVO Erwägungsgrund 85`. Split texts over 400 words into parts with 50-word overlap and suffix `-p2` … Stable `chunk_id` = `<reg>-<lang>-<unit_type>-<article>-<paragraph|0>-<version_date>[-pN]`, lowercase.
> 3. **`src/eucc/contract.py`:** articles, recitals and annexes per entry = `expected_units`; no empty texts; no duplicate `chunk_id`. Clear error naming the entry.
> 4. **Local CLI:** `python -m eucc.build_corpus --raw data/raw --out data/build` → `silver_provisions.jsonl`, `gold_chunks.jsonl`, summary table.
> 5. **Tests:** small excerpts from the real files in `tests/fixtures/` (consolidated article with paragraphs and a ►C1 marker, deleted provision, recital, annex — EN and DE).
>
> Don't commit. Run `uv run python -m pytest` and `uv run ruff check .`.

## Step 3 — Local run and contract

```powershell
uv run python -m eucc.build_corpus --raw data/raw --out data/build
```

**First run (ours):**

```
entry                 articles  paragraphs  recitals  annexes  chunks  max words
gdpr-consolidated-en        99         372         0        0     398        400
...
aia-consolidated-en        119         552         0       14     617        400
...
```

How to read it:

- **EN and DE have identical structure counts** (372 / 552 / 254 paragraphs, same articles and recitals) — two independently parsed languages agreeing is strong evidence the parser is right.
- `max words = 400` everywhere → long units are split as intended; chunks slightly above provisions are split parts.
- **14 annexes for the AI Act** although the spike said I–XIII. Check which:

```powershell
Get-Content data\build\silver_provisions.jsonl -Encoding UTF8 | ConvertFrom-Json | Where-Object { $_.reg -eq 'AIA' -and $_.lang -eq 'en' -and $_.unit_type -eq 'annex' } | Select-Object article, heading | Format-Table -AutoSize
```

**Actual:** I … XIII plus **XIV with an empty heading**. Annex XIV is real: it was inserted by the Omnibus (`▼M1`, "The list of codes, categories and corresponding types of AI systems … referred to in Article 30"). The spike had stopped at XIII. Fixes:

1. manifest `expected_units.annexes` for the AI Act → **14**; spike note → "Annexes I–XIV (XIV inserted by M1)"; contract also checks annexes,
2. annexes with an empty title use their first paragraph as heading (truncated to 120 characters), with a test on Annex XIV.

After the fixes:

```powershell
uv run python -m pytest
uv run python -m eucc.build_corpus --raw data/raw --out data/build
```

**Expected:** `166 passed`; final table

```
entry                 articles  paragraphs  recitals  annexes  chunks  max words
gdpr-consolidated-en        99         372         0        0     398        400
gdpr-consolidated-de        99         372         0        0     397        400
gdpr-original-en             0           0       173        0     176        400
gdpr-original-de             0           0       173        0     176        400
aia-consolidated-en        119         552         0       14     617        400
aia-consolidated-de        119         552         0       14     611        400
aia-original-en              0           0       180        0     198        400
aia-original-de              0           0       180        0     195        400
dora-original-en            64         254       106        0     385        400
dora-original-de            64         254       106        0     381        400
omnibus-original-en          4           0        47        0      86        400
omnibus-original-de          4           0        47        0      84        400
```

Total: **3,704 chunks**.

## Step 4 — Spot-check the content

GDPR Art. 33(1) in both languages:

```powershell
Get-Content data\build\gold_chunks.jsonl -Encoding UTF8 | ConvertFrom-Json | Where-Object { $_.chunk_id -like 'gdpr-*-article-33-1-*' } | Select-Object chunk_id, chunk_text | Format-List
```

**Actual:**

```
chunk_id   : gdpr-en-article-33-1-2016-05-04
chunk_text : GDPR Art. 33(1) — Notification of a personal data breach to the supervisory authority
             In the case of a personal data breach, the controller shall without undue delay and,
             where feasible, not later than 72 hours after having become aware of it, notify ...

chunk_id   : gdpr-de-article-33-1-2016-05-04
chunk_text : DSGVO Art. 33 Abs. 1 — Meldung von Verletzungen des Schutzes personenbezogener Daten an die Aufsichtsbehörde
             Im Falle einer Verletzung des Schutzes personenbezogener Daten meldet der Verantwortliche
             unverzüglich und möglichst binnen 72 Stunden ...
```

The German source has an inline corrigendum span `►C1 … ◄` in exactly this paragraph — it is gone, umlauts are intact.

Omnibus articles (the reg code in ids is `omnibus_ai`):

```powershell
Get-Content data\build\gold_chunks.jsonl -Encoding UTF8 | ConvertFrom-Json | Where-Object { $_.chunk_id -like '*omnibus*' -and $_.unit_type -ne 'recital' } | Select-Object chunk_id | Format-Table -AutoSize
```

**Actual:** `omnibus_ai-en-article-1-0-2026-07-24` … `-p32` (Art. 1 is the long list of amendments), plus Arts. 2, 3 (2 parts) and 4; DE has 30 parts for Art. 1.

Known limitation (backlog): Omnibus Art. 1 is split by word count, so a part can start mid-amendment. Splitting at its numbered points would sharpen "what changed" questions; v1.0 answers those from the consolidated text.

## Step 5 — Bundle job on Databricks

Claude Code:

> Read C:\dev\energy-lakehouse-de\databricks.yml and its `resources/` (read-only) to copy the **wheel + serverless job** pattern.
> 1. Console entry point `build_corpus_job`: reads `/Volumes/<catalog>/<schema>/raw/eurlex`, parses, chunks, runs the contract, writes `<catalog>.<schema>.silver_provisions` and `<catalog>.<schema>.gold_chunks` with an explicit Spark schema; parameters `--catalog`, `--schema`.
> 2. `gold_chunks` with `delta.enableChangeDataFeed = true`, written idempotently: `MERGE INTO` on `chunk_id` (update changed, insert new, `WHEN NOT MATCHED BY SOURCE THEN DELETE`). `silver_provisions` may be overwritten. Print counts and MERGE metrics.
> 3. `resources/build_corpus.job.yml`: job `build_corpus`, one wheel task, serverless environment with the wheel, `beautifulsoup4`, `lxml`; parameters from bundle variables (dev → `compliance_dev`).
> 4. Spark code in a thin `src/eucc/jobs/build_corpus.py`; logic stays in the tested functions.
>
> Run tests, ruff, `databricks bundle validate -t dev -p free`.

Deploy and run in dev:

```powershell
databricks bundle validate -t dev -p free
databricks bundle deploy -t dev -p free
databricks bundle run build_corpus -t dev -p free
```

**Expected:** `Validation OK!`; run `TERMINATED SUCCESS` after a few minutes; log shows ~3,704 chunks inserted.

Run it a second time:

```powershell
databricks bundle run build_corpus -t dev -p free
```

**Expected:** `inserted 0, updated 0, deleted 0` — the job is idempotent.

## Step 6 — Verify in the SQL editor

```sql
SELECT reg, lang, unit_type, COUNT(*) AS chunks
FROM workspace.compliance_dev.gold_chunks
GROUP BY ALL ORDER BY reg, lang, unit_type;

SELECT COUNT(*) AS total, COUNT(DISTINCT chunk_id) AS distinct_ids
FROM workspace.compliance_dev.gold_chunks;

SHOW TBLPROPERTIES workspace.compliance_dev.gold_chunks ('delta.enableChangeDataFeed');
```

**Actual:**

```
total  distinct_ids
3704   3704

delta.enableChangeDataFeed  true
```

Same count as the local run, no duplicate ids, CDF on — ready for the Delta Sync index.

## Step 7 — Commit and PR

```powershell
git branch --show-current
git status --short
git add src tests resources databricks.yml pyproject.toml uv.lock corpus/manifest.yaml docs
git commit -m "feat(corpus): parse EUR-Lex XHTML into provisions and citable chunks; build_corpus job (closes #7, closes #8)"
git push -u origin s1-4/parse-chunk
gh pr create --fill --base master
gh pr checks --watch
gh pr merge --squash --delete-branch
git switch master
git pull
```

**Expected:** `data/` is not in `git status`; `ci pass`; #7 and #8 closed.

## Checkpoint

- [x] Parser for consolidated (ELI) and original (OJ) markup, markers stripped, `amended_by` kept
- [x] Chunks with bilingual citation headers, stable ids, 400-word split with overlap
- [x] Contract on articles, recitals, annexes, empty texts, duplicate ids
- [x] `silver_provisions` and `gold_chunks` (CDF) in `compliance_dev` via bundle job, idempotent MERGE
- [x] 3,704 chunks locally = 3,704 on Databricks

## Troubleshooting (real problems from this step)

| Symptom | Cause | Fix |
| --- | --- | --- |
| AI Act shows 14 annexes, manifest says 13 | Annex XIV inserted by the Omnibus (▼M1); spike stopped at XIII | Trust the parser after checking the source; manifest annexes = 14 |
| Annex XIV has an empty heading | Its title is a plain paragraph, not the usual subtitle element | Fall back to the first paragraph (≤ 120 chars) |
| Query `-like 'omnibus-en-article-*'` returns nothing | Reg code in ids is `omnibus_ai` | Search `-like '*omnibus*'` or use the manifest reg code |
| German umlauts garbled when reading `.jsonl` in PowerShell | Windows PowerShell 5.1 reads files as ANSI by default | `Get-Content … -Encoding UTF8` |
| Omnibus shows 0 paragraphs | Its articles are amendment lists without numbered paragraphs | Expected; Art. 1 is split by word count (backlog: split at amendment points) |

## What you learned

- Turning legal XHTML into provisions with a structure-aware parser (ELI ids, two markup variants).
- Why chunks carry a citation header, and how stable ids plus MERGE make the pipeline idempotent.
- A data contract that checks counts against the manifest — it caught a wrong manual number twice.
- Running the same tested logic locally and as a serverless wheel job, with identical results.

## Tested on

2026-11-05 · Windows 11 · Windows PowerShell 5.1 · Python 3.12 / uv · BeautifulSoup 4 + lxml · Databricks Asset Bundles · Databricks Free Edition (serverless)
