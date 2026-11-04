# Sprint 1a — Identity, corpus manifest and extraction

Project 3 · EU Compliance Copilot · Tutorial 01

> Goes to: `docs/tutorial/sprint1a-identity-corpus.md` · Covers issues #4 (S1-1), #5 (S1-2), #6 (S1-3), #11 (S1-8)

## Goal

At the end of this tutorial:

- the dev container sets the Databricks host and profile automatically,
- CI talks to Databricks as its own service principal `sp-eucc-cicd`, which owns staging and prod,
- a versioned **corpus manifest** lists 12 official EU sources (GDPR, AI Act, DORA, AI Omnibus × EN/DE),
- an **extractor** downloads them from the Publications Office (Cellar), validates article and recital counts, pins them with a sha256 lock file and uploads them to a Unity Catalog volume.

## Concepts

**One identity per project.** Project 3 gets its own service principal, never Project 1's. Staging and prod schemas are owned by it; dev stays yours. The OAuth secret is created by hand and stored only in GitHub secrets — a Terraform-managed secret would sit in plain text in the state file.

**Shared catalog, two Terraform states.** Both projects use the catalog `workspace`. `databricks_grants` (plural) is *authoritative*: it replaces the complete grant list of an object, so two states would delete each other's grants. Use `databricks_grant` (singular) on shared securables — it only manages one principal on one object.

**Consolidated vs original acts.** EUR-Lex publishes *consolidated* texts (all amendments merged, documentation only) and the *original* acts (legally authentic, in the Official Journal). Consolidated texts contain **no recitals**, so recitals come from the originals. Corrigenda are published per language, so EN and DE of the "same" version can have different revisions.

**Website ≠ API.** The EUR-Lex website answers scripts with a bot challenge (HTTP 202, 0 bytes). We do not work around it. The Publications Office offers an official machine interface, **Cellar**, with HTTP content negotiation.

**Lock file as freshness signal.** CI cannot commit to `master`, so download hashes go into `corpus/manifest.lock.json`, committed once via PR. Every later run compares against it; a changed hash means the source changed and the run fails visibly.

**Validate against expected counts.** The manifest records how many articles and recitals each source must contain. The first real run caught an error in the manual analysis itself.

## Prerequisites

- Sprint 0 finished (repo, ruleset, schemas via Terraform, Vector Search endpoint)
- Windows PowerShell for `gh`; dev container for Terraform

**Rule used throughout:** `git` works in both Windows and the container (same folder), **`gh` only on Windows** — the container has no GitHub login by design.

## Part A — Sprint planning

### Step 1 — Create the 8 stories

The planning script `create-sprint1-issues.ps1` creates issues S1-1 … S1-8 with acceptance criteria in milestone `Sprint 1`. Downloaded scripts carry the *Mark of the Web*:

```powershell
Unblock-File .\create-sprint1-issues.ps1
.\create-sprint1-issues.ps1
```

**Expected:** 8 lines `S1-x … -> https://github.com/.../issues/N`.

The script's last step (milestone description) failed with 404 in our run. **Do not re-run a partly failed script** — it created duplicates #12–#19. Clean up:

```powershell
12..19 | ForEach-Object { gh issue close $_ -R tutul032/eu-compliance-copilot --reason "not planned" --comment "Duplicate of #$($_ - 8)"; gh issue edit $_ -R tutul032/eu-compliance-copilot --add-label duplicate --milestone "" }
gh issue list -R tutul032/eu-compliance-copilot --milestone "Sprint 1"
```

**Expected:** exactly 8 open issues, #4–#11.

Set the sprint goal by milestone number (`<n>` is a placeholder — use the number from the first command):

```powershell
gh api repos/tutul032/eu-compliance-copilot/milestones --jq '.[] | [.number, .title] | @tsv'
gh api -X PATCH repos/tutul032/eu-compliance-copilot/milestones/2 -f description="Goal: GDPR, AI Act (post-Omnibus) and DORA in EN+DE as article-level chunks in Vector Search; GDPR Art. 33 found in both languages; recall@5 baseline measured." --jq '[.title, .open_issues] | @tsv'
```

**Expected:** `Sprint 1  8`.

## Part B — S1-8 Dev container environment

### Step 2 — One `remoteEnv` block

```powershell
git switch master; git pull; git switch -c s1-8/devcontainer-remoteenv
```

In `.devcontainer/devcontainer.json` there must be **exactly one** `remoteEnv` key — with two, the last one silently wins. Final block:

```jsonc
"remoteEnv": {
  "PATH": "/home/vscode/.venvs/eu-compliance-copilot/bin:${containerEnv:PATH}",
  "TF_VAR_databricks_host": "https://dbc-405898bc-c90e.cloud.databricks.com",
  "DATABRICKS_CONFIG_PROFILE": "free"
},
```

Also rename all Project 1 leftovers in `.devcontainer/` (`name`, `UV_PROJECT_ENVIRONMENT`, `python.defaultInterpreterPath`, README). Verify **before** rebuilding:

```bash
grep -rn -i "energy" .devcontainer/
grep -c '"remoteEnv"' .devcontainer/devcontainer.json
```

**Expected:** no output, then `1`.

### Step 3 — Rebuild and verify

`Ctrl+Shift+P` → **Dev Containers: Rebuild Container**, then a **new** terminal:

```bash
echo $TF_VAR_databricks_host
echo $DATABRICKS_CONFIG_PROFILE
which python
databricks auth login --host $TF_VAR_databricks_host --profile free
cd /workspaces/eu-compliance-copilot/infra/terraform && terraform plan -input=false
```

**Expected:** host URL, `free`, `/home/vscode/.venvs/eu-compliance-copilot/bin/python`, `Profile free was successfully saved`, `No changes.`

Commit, PR (`closes #11`), merge.

## Part C — S1-1 Service principal and CI identity

### Step 4 — Check how Project 1 manages identities

```powershell
cd C:\dev\energy-lakehouse-de
git ls-files "*.tf"
git grep -n -E "databricks_service_principal|service_principal_secret|databricks_grants|databricks_group" -- "*.tf"
Get-Content terraform\grants.tf
cd C:\dev\eu-compliance-copilot
```

**Expected:** SPs and group in `terraform/identities.tf`, grants in `grants.tf` using **`databricks_grant` (singular)** only, no secret resource. → Project 3 can add its own catalog grant without touching Project 1.

### Step 5 — Terraform for `sp-eucc-cicd`

```powershell
git switch master; git pull; git switch -c s1-1/sp-eucc-cicd
```

Claude Code prompt:

> In C:\dev\eu-compliance-copilot on branch `s1-1/sp-eucc-cicd`. Read C:\dev\energy-lakehouse-de\terraform\ (identities.tf, grants.tf, schemas.tf, variables.tf) as a **read-only** reference. In `infra/terraform/`:
> 1. `identities.tf`: `databricks_service_principal` "cicd", display_name `sp-eucc-cicd`, same attributes/entitlements as Project 1's `cicd`.
> 2. `variables.tf`: `catalog` (default "workspace") and `platform_owner` (my user email).
> 3. Schemas: `compliance_staging` and `compliance_prod` owner = the SP's application_id; `compliance_dev` keeps my user.
> 4. `grants.tf` with **`databricks_grant` (singular) only**: `USE_CATALOG` on `var.catalog` for the SP; `SELECT` + `USE_SCHEMA` for `var.platform_owner` on staging and prod. Never `databricks_grants` (plural) — the catalog is shared with Project 1's state.
> 5. No `databricks_service_principal_secret`.
> 6. `docs/adr/0003-identity-and-ownership.md`.
>
> Run `terraform fmt` and `terraform validate`. No plan, apply or commit.

Plan in the container — always warm up auth first:

```bash
databricks current-user me --profile free > /dev/null && echo "auth ok"
cd /workspaces/eu-compliance-copilot/infra/terraform
terraform plan -out=tfplan -input=false
```

**Expected:** `Plan: 4 to add, 2 to change, 0 to destroy.` — SP, catalog grant, 2 owner-read grants; owner change on staging and prod; nothing on dev.

Commit **from the repo root** (paths are relative to the current folder):

```bash
cd /workspaces/eu-compliance-copilot
git add infra/terraform/*.tf docs/adr/0003-identity-and-ownership.md
git status --short
git commit -m "infra: sp-eucc-cicd, catalog grant, SP-owned staging/prod schemas (refs #4)"
git push -u origin s1-1/sp-eucc-cicd
```

**Expected:** `tfplan` is **not** staged. PR on Windows, merge.

### Step 6 — Apply from master

```bash
git switch master && git pull
cd infra/terraform
terraform plan -out=tfplan -input=false
terraform apply tfplan
terraform state show databricks_service_principal.cicd | grep -E "application_id|display_name"
databricks schemas list workspace | grep compliance
```

**Expected (our run):**

```
Apply complete! Resources: 4 added, 2 changed, 0 destroyed.
    application_id             = "0af2526a-d869-4722-8988-c45fffd0144b"
    display_name               = "sp-eucc-cicd"
workspace.compliance_dev      rezaul.tutul@yahoo.com
workspace.compliance_prod     0af2526a-d869-4722-8988-c45fffd0144b
workspace.compliance_staging  0af2526a-d869-4722-8988-c45fffd0144b
```

If the plan on `master` says **"No changes"** although you expect changes: the PR is not merged or not pulled. Stop and check `git log --oneline -3`.

### Step 7 — OAuth secret and GitHub secrets

Databricks → **Settings → Identity and access → Service principals → sp-eucc-cicd → Secrets → Generate secret**, lifetime 365 days. Keep the dialog open:

```powershell
gh secret set DATABRICKS_HOST -R tutul032/eu-compliance-copilot --body "https://dbc-405898bc-c90e.cloud.databricks.com"
gh secret set DATABRICKS_CLIENT_ID -R tutul032/eu-compliance-copilot --body "0af2526a-d869-4722-8988-c45fffd0144b"
gh secret set DATABRICKS_CLIENT_SECRET -R tutul032/eu-compliance-copilot
gh secret list -R tutul032/eu-compliance-copilot
```

The last `set` has no `--body`: paste the secret at the prompt, so it never lands in shell history. **Expected:** three secrets listed. Record the expiry date in `docs/runbook.md`.

### Step 8 — CI validates the bundle as the SP

```powershell
git switch master; git pull; git switch -c s1-1/ci-bundle-validate
```

Claude Code: add a step to `ci.yml` that installs the Databricks CLI (same pinned action SHA as Project 1) and runs `databricks bundle validate -t staging` with the three secrets as step-level env; add `run_as: service_principal_name: 0af2526a-…` for staging and prod in `databricks.yml`; create `docs/runbook.md` with the identity table and the secret rotation procedure.

```powershell
git add .github/workflows/ci.yml databricks.yml docs/runbook.md
git commit -m "ci: validate bundle against staging as sp-eucc-cicd (closes #4)"
git push -u origin s1-1/ci-bundle-validate
gh pr create --fill --base master
gh pr checks --watch
```

**Expected:** "no checks reported" for up to ~30 s right after creating the PR is normal; then `ci/ci (pull_request)` passes and the log shows `Validation OK!`.

## Part D — S1-2 Source spike and corpus manifest

### Step 9 — Inspect the sources on EUR-Lex

For each regulation open `https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:<celex>` → **Show consolidated versions**; read the header line `CELEX — LANG — DATE — REVISION`.

**Our findings (2026-11-04):**

| Doc | Text used | CELEX | Version / revision |
|---|---|---|---|
| GDPR | articles | 02016R0679-20160504 | EN 000.002 (C1), DE 000.003 (C1–C3) |
| GDPR | recitals (173) | 32016R0679 | original OJ L 119, 4.5.2016 |
| AI Act | articles, annexes | 02024R1689-20260727 | EN 001.001, DE 001.002 — includes M1 = Reg. 2026/1744 |
| AI Act | recitals (180) | 32024R1689 | original OJ L 2024/1689 |
| DORA | articles + recitals (106) | 32022R2554 | no consolidated version exists |
| AI Omnibus | amendments + recitals (47) | 32026R1744 | original OJ L 2026/1744 |

Key observations:

- The consolidated AI Act dated **27.07.2026** already contains the Omnibus: page 1 lists *Amended by ►M1 Regulation (EU) 2026/1744*, and Art. 113 reads 2 December 2027 (Annex III) / 2 August 2028 (Annex I).
- Consolidated texts start with Chapter I — **no recitals**.
- The AI Act has **119 article units**: 113 plus inserted 4a, 60a, 75a–75d → `article` must be a string.
- Markers to strip and keep as metadata: block markers `▼B`, `▼M1`; inline spans `►C1 … ◄`; deleted provisions `▼M1` followed by `__________`.

### Step 10 — Inspect the HTML markup

Open the consolidated HTML, right-click "Article 33" → **Inspect**:

```html
<div class="eli-subdivision" id="art_33">
  <p class="title-article-norm">Article 33</p>
  <div class="eli-title" id="art_33.tit_1">
    <p class="stitle-article-norm">Notification of a personal data breach …</p>
  </div>
  <div class="norm">
    <span class="no-parag">1.&nbsp;&nbsp;</span>
    <div class="norm inline-element">…</div>
  </div>
  <div class="norm">…</div>   <!-- paragraphs 2–5 -->
</div>
```

**Decision:** parse HTML (not PDF) with BeautifulSoup on ELI ids. Article number from the element id; paragraph = each direct `div.norm` child, number from `span.no-parag`. PDF has page headers and soft hyphens in every page. Original OJ acts use `oj-` classes → second parser variant.

Record everything in `docs/spikes/s1-2-eurlex-format.md`.

### Step 11 — Manifest and validator

```powershell
git switch master; git pull; git switch -c s1-2/corpus-manifest
```

Claude Code: create `corpus/manifest.yaml` with 12 entries (6 sources × en/de: `id, reg, celex, lang, role, version_date, revision, source_url, format, expected_units, sha256: null`), a pydantic loader `src/eucc/corpus/manifest.py` (unique ids, both languages per source, URL contains CELEX and language), and `tests/test_manifest.py`.

```powershell
uv run python -m pytest
```

**Expected:** all green including `test_repo_manifest_is_valid` (12 entries). If it fails with `FileNotFoundError … corpus\manifest.yaml`, the YAML was not written — ask Claude Code to create it from the spike table.

Also update `aia-04` in `eval/questions.yaml`: required source is AI Act Art. 113 only (the consolidated text already contains the new dates). Commit with `closes #5`.

## Part E — S1-3 Extraction

### Step 12 — `raw` volume per environment

```powershell
git switch master; git pull; git switch -c s1-3/raw-volume
```

Claude Code: `infra/terraform/volumes.tf` with one MANAGED `databricks_volume` "raw" per schema (`for_each` like the schemas; owner = your user for dev, the SP for staging/prod).

```bash
databricks current-user me --profile free > /dev/null && echo "auth ok"
terraform plan -out=tfplan -input=false
```

**Expected:** `Plan: 3 to add, 0 to change, 0 to destroy.` PR (`refs #6`), merge, apply from master:

```bash
for s in dev staging prod; do databricks volumes list workspace compliance_$s | grep raw; done
```

**Expected:** three lines `raw`.

### Step 13 — Find a download channel

Test the EUR-Lex website with a script:

```powershell
$ua = "eucc-extractor/0.1 (+https://github.com/tutul032/eu-compliance-copilot)"
curl.exe -s -o $env:TEMP\gdpr_cons.html -w "%{http_code} %{size_download} %{content_type}`n" -A $ua "https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:02016R0679-20160504"
```

**Actual:** `202 0 text/html` — a bot challenge. Not worked around. Test Cellar instead:

```powershell
curl.exe -sL -o $env:TEMP\cellar_orig_en.html -w "%{http_code} %{size_download} %{content_type}`n" -A $ua -H "Accept: application/xhtml+xml, text/html;q=0.9" -H "Accept-Language: eng" "http://publications.europa.eu/resource/celex/32016R0679"
curl.exe -sL -o $env:TEMP\cellar_cons_de.html -w "%{http_code} %{size_download} %{content_type}`n" -A $ua -H "Accept: application/xhtml+xml, text/html;q=0.9" -H "Accept-Language: deu" "http://publications.europa.eu/resource/celex/02016R0679-20160504"
(Select-String -Path $env:TEMP\cellar_orig_en.html -Pattern 'id="rct_' -AllMatches).Matches.Count
```

**Actual:** `200 806864 application/xhtml+xml`, `200 503459 application/xhtml+xml`, and **173** recital ids. Same ELI markup as the website. Languages are three-letter codes: `eng`, `deu`.

The manifest keeps two URLs: `source_url` (EUR-Lex, for human citations in answers) and the computed `fetch_url` (Cellar, for the extractor).

### Step 14 — Extractor module

```powershell
git switch master; git pull; git switch -c s1-3/extractor
```

Claude Code builds `src/eucc/extract.py`: GET `fetch_url` with `Accept` and `Accept-Language` headers and a generic User-Agent; 3 retries with backoff on 429/5xx; a clear error on 202 ("bot challenge — use Cellar"); validation (XHTML, > 50 kB, unique `id="art_…"` / `id="rct_…"` counts equal `expected_units`, language word); output `data/raw/<celex>/<lang>/<version_date>.xhtml`; lock file `corpus/manifest.lock.json` (`--write-lock`, otherwise compare and fail on mismatch); `--upload-to` via `databricks-sdk` `files.upload`; 2 s pause between requests. Tests with `responses`, no real network. `data/` in `.gitignore`.

### Step 15 — First real run

```powershell
uv run python -m eucc.extract --out data/raw --write-lock
```

**Actual first run:**

```
ERROR omnibus-original-en: found 4 articles, expected 7
ERROR omnibus-original-de: found 4 articles, expected 7
```

The extractor was right: the Omnibus has 4 articles (amending 2024/1689, 2018/1139, 2023/1230, entry into force). The 7 from the manual PDF count included **Articles 75b–75d quoted inside Art. 1** as new AI Act text — they have no ELI ids. Fix `expected_units` to 4 in the manifest and the spike note, then:

```powershell
uv run python -m eucc.extract --out data/raw --write-lock
uv run python -m eucc.extract --out data/raw
$LASTEXITCODE
```

**Expected:**

```
id                    status    bytes  articles  recitals
gdpr-consolidated-en  ok       462324        99         0
gdpr-consolidated-de  ok       503459        99         0
gdpr-original-en      ok       806864        99       173
gdpr-original-de      ok       862613        99       173
aia-consolidated-en   ok       851286       119         0
aia-consolidated-de   ok       901043       119         0
aia-original-en       ok      1262391       113       180
aia-original-de       ok      1338179       113       180
dora-original-en      ok       746866        64       106
dora-original-de      ok       783994        64       106
omnibus-original-en   ok       351287         4        47
omnibus-original-de   ok       374753         4        47
0
```

Byte sizes are identical across runs → Cellar files are deterministic and the lock is stable.

### Step 16 — Upload to the dev volume

```powershell
$env:DATABRICKS_CONFIG_PROFILE = "free"
uv run python -m eucc.extract --out data/raw --upload-to /Volumes/workspace/compliance_dev/raw/eurlex
databricks fs ls dbfs:/Volumes/workspace/compliance_dev/raw/eurlex -p free
databricks fs ls dbfs:/Volumes/workspace/compliance_dev/raw/eurlex/02024R1689-20260727/de -p free
```

**Expected:**

```
02016R0679-20160504
02024R1689-20260727
32016R0679
32022R2554
32024R1689
32026R1744
2026-07-27.xhtml
```

Commit **including** `corpus/manifest.lock.json`, **excluding** `data/`:

```powershell
git add .gitignore corpus src tests docs/spikes pyproject.toml uv.lock
git status --short
git commit -m "feat(extract): Cellar extractor with validation, lock file and volume upload (refs #6)"
```

Scope decision: the planned extractor container and scheduled GitHub workflow moved to the backlog; #6 closed after this PR.

## Checkpoint

- [x] `remoteEnv` sets host and profile after rebuild (#11)
- [x] `sp-eucc-cicd` via Terraform, owns staging/prod; CI validates the bundle as the SP (#4)
- [x] Spike note + 12-entry manifest with validator (#5)
- [x] `raw` volumes; 12 sources downloaded from Cellar, validated, locked, uploaded to dev (#6)

## Troubleshooting (real problems from this sprint)

| Symptom | Cause | Fix |
| --- | --- | --- |
| `…ps1 is not digitally signed` | Downloaded file has the Mark of the Web | `Unblock-File .\script.ps1` |
| Issues #12–#19 duplicated | Partly failed script was re-run | Check what exists first; close duplicates as "not planned" with `duplicate` label |
| Milestone PATCH 404 | Placeholder `<n>` not replaced / PS 5.1 `ConvertFrom-Json` returns the array as one object | Read the number via `--jq '… \| @tsv'` and type it |
| Env vars empty despite `remoteEnv` | Two `remoteEnv` keys; the last one wins | Merge into one block |
| `argument "free" cannot be combined with --host or --profile` | `$TF_VAR_databricks_host` was empty, shifting the arguments | Fix `remoteEnv`; guard scripts with `${VAR:?not set}` |
| `which python` shows `.venvs/energy-lakehouse-de` | Copy-adapt leftovers in `.devcontainer/` | `grep -rn -i energy .devcontainer/` and rename |
| `cannot configure default credentials … profile=free` | Token expired, or the profile is gone after a rebuild (no `host=` in the error) | `databricks current-user me --profile free` before every plan; `databricks auth login` |
| `gh: To get started … gh auth login` in the container | `gh` is only logged in on Windows (by design) | Run `gh` in Windows PowerShell |
| `pathspec 'infra/terraform' did not match` | Command run inside `infra/terraform` | `cd` to repo root first |
| `M infra/terraform/tfplan` keeps appearing | The PR that untracked it (#3) was created but never merged | `gh pr list --state open` at the end of each day; merge, then `git merge origin/master` |
| Plan on master shows "No changes" | Feature PR not merged/pulled yet | Check `git log`; never apply when the plan surprises you |
| `no checks reported` right after `gh pr create` | Workflow start takes a few seconds | Wait ~30 s; otherwise check `mergeable` and YAML validity |
| EUR-Lex returns `202`, 0 bytes | Bot challenge on the website | Use Cellar: `publications.europa.eu/resource/celex/<CELEX>` with `Accept` + `Accept-Language` |
| `found 4 articles, expected 7` (Omnibus) | Manual count included quoted amendment text | Trust ELI-id counts; fix manifest and spike |
| `Copy-Item … Downloads\… does not exist` | Download still in progress | Wait and retry |

## What you learned

- One least-privilege identity per project; secrets outside Terraform state; non-authoritative grants on shared securables.
- How EU law is published: consolidated vs original, per-language revisions, amendment markers, no recitals in consolidated texts.
- Choosing a source format from evidence (HTML with ELI ids vs PDF).
- Respecting bot protection and using the official machine interface (Cellar content negotiation).
- Data contracts at ingestion: expected counts and a sha256 lock file that turns "the source changed" into a failing check.

## Tested on

2026-11-03 – 2026-11-04 · Windows 11 · Windows PowerShell 5.1 · gh CLI · VS Code + Claude Code · dev container (Terraform, Databricks provider v1.139.1) · Python 3.12 / uv · Databricks Free Edition (serverless)
