# Sprint 0 — Foundation: from an empty folder to a protected, tested repo with infrastructure as code

Project 3 · EU Compliance Copilot · Tutorial 00

> Goes to: `docs/tutorial/sprint0-foundation.md` · Screenshots: `docs/tutorial/img/sprint0/`

## Goal

Start a new industry-style project the way a professional team does when it already has one working project: **reuse what is generic, start fresh what is project-specific**. At the end you have:

- a discovery package (problem statement, 40-question evaluation set, licence check),
- a GitHub repo with fresh history and the same branch protection as Project 1,
- a scaffold copied-adapted from Project 1 (dev container, CI, bundle, Terraform, Python package, tests),
- three Unity Catalog schemas (`compliance_dev|staging|prod`) created by Terraform from reviewed code on `master`,
- a board with milestones, and a Vector Search endpoint that answers a German question with an English article.

## Concepts

**Discovery before code.** Industry projects start with *what problem, for whom, how measured, are we allowed to use the data*. The output is three artefacts: a problem statement with success metrics, a question set that later becomes the automated evaluation, and a licence check.

**Evaluation set as a contract.** The 40 questions in `eval/questions.yaml` define "good" before any model exists. Each item lists the expected behaviour (`answer`, `tool`, `refuse`, `out_of_scope`, `correct_premise`), the articles that must be cited, and the key facts. CI will grade every release against it.

**Versioned corpus.** Law changes. The AI Act was amended by the Digital Omnibus on AI (Regulation (EU) 2026/1744, in force 27 July 2026), which moved the Annex III high-risk date from 2 August 2026 to 2 December 2027. Every chunk therefore carries `celex` and `version_date`, and question `aia-04` checks that the copilot knows the new date.

**Reusing a working project — four levels.**

| Level | What | When |
| --- | --- | --- |
| 1 Copy-adapt | Copy generic files into a fresh repo, rename | 2nd project (us) |
| 2 Template repo | GitHub template / copier / cookiecutter | 3+ similar projects |
| 3 Shared building blocks | Reusable workflows (`workflow_call`), Terraform modules, internal packages, `databricks bundle init` templates | Platform team |
| 4 Golden path | Backstage "create service" button | Large companies |

Rule of three: copy the second time, extract a template the third time.

**Never carried over:** git history, Terraform state, secrets, `.venv/`, `.terraform/`, data, service principals. Each project gets its own state and its own least-privilege identities. The Databricks workspace and the CLI profile are shared environment, not project.

**Plan from master, apply a saved plan.** Infrastructure changes are reviewed in a PR, merged, then planned again from `master` and applied from the saved plan file. What runs is exactly what was reviewed.

## Prerequisites

- Project 1 (`C:\dev\energy-lakehouse-de`) with dev container, CI and Terraform working
- `gh` logged in, `uv`, VS Code + Claude Code, Docker Desktop
- Databricks Free Edition workspace, CLI profile `free`

## Part A — Discovery

### Step 1 — Check platform limits

In the Databricks UI check **Serving** for available models and note the exact endpoint names.

**Expected:** chat models (`gpt-oss-120b`, `qwen35-122b`, `llama-3-3-70b`, …) and embedding models (`gte-large-en`, `bge-large-en`, `qwen3-embedding-0-6b`).

Read the Free Edition limitations page. Design consequences:

| Limit | Consequence |
| --- | --- |
| One Vector Search endpoint, one unit, no Direct Vector Access | Delta Sync index on a table with Change Data Feed; one endpoint shared by dev/staging/prod |
| Restricted outbound internet | EUR-Lex download runs outside Databricks (container / GitHub Actions) |
| One App, stops after 24 h | Restart the UI before demos |
| Non-commercial use only | Fine for a portfolio — state it in the README |

`gte-large-en` and `bge-large-en` are English-only. German questions need `qwen3-embedding-0-6b` (multilingual) → ADR-0002.

### Step 2 — Write the problem statement

Create `docs/discovery/discovery.md` with: problem statement, personas, in/out of scope, success metrics with targets, licence table, platform constraints, key decisions, risks, sprint plan.

**Expected:** a reader understands in two minutes *who* the copilot is for, *what* it must never do (legal advice, invented articles), and *how* success is measured (citation hit rate ≥ 90 %, refusal correctness 100 %, p95 latency < 10 s).

### Step 3 — Build the question set

Create `eval/questions.yaml` with 40 items: GDPR 11, AI Act 10, DORA 8, cross-regulation 3, tool questions on Project 1 data 4, adversarial/out-of-scope 4; 9 of them in German.

Then review every item against the EUR-Lex text and set `reviewed: true`.

**Expected:** 40 items, all `reviewed: true`. Prioritise items that the Omnibus may have changed (`aia-04`, `aia-08`).

📸 `img/sprint0/01-questions-yaml.png`

### Step 4 — Licence check

| Source | Terms | Consequence |
| --- | --- | --- |
| EUR-Lex texts | Commission Decision 2011/833/EU: reuse for commercial and non-commercial purposes, acknowledge source, do not distort meaning | Attribution in README, UI footer, model card |
| Authenticity | Only the Official Journal is authentic | Footer: "Not legal advice. Source: EUR-Lex, consolidated text as of <date>" |
| Models | gpt-oss Apache-2.0, Llama 3.3 Community Licence, Qwen Apache-2.0 | Note in model card |
| Databricks Free Edition | Non-commercial | Portfolio OK |

Copy the exact attribution line from the EUR-Lex legal notice into `docs/discovery/licences.md`.

📸 `img/sprint0/02-eurlex-legal-notice.png`

## Part B — Repository

### Step 5 — Create the repo (PowerShell)

```powershell
cd C:\dev
gh repo create tutul032/eu-compliance-copilot --public --clone --add-readme --license mit --gitignore Python --description "Bilingual RAG + agent copilot for GDPR, AI Act and DORA on Databricks"
cd C:\dev\eu-compliance-copilot
git log --oneline
```

**Expected:** `Created repository …`, `Cloned`, and exactly one commit `Initial commit`. This is the only commit that ever lands directly on `master`.

### Step 6 — Copy branch protection from Project 1

List Project 1's rulesets:

```powershell
gh api repos/tutul032/energy-lakehouse-de/rulesets --jq '.[] | [.id, .name] | @tsv'
```

**Expected (our case):** two rulesets.

```
25568319        master
25590876        protect-master
```

Inspect both before copying:

```powershell
gh api repos/tutul032/energy-lakehouse-de/rulesets/25568319 --jq '{name, enforcement, rules: [.rules[].type]}'
gh api repos/tutul032/energy-lakehouse-de/rulesets/25590876 --jq '{name, enforcement, rules: [.rules[].type]}'
```

**Expected:** `master` is `disabled` (leftover); `protect-master` is `active` with `deletion`, `non_fast_forward`, `pull_request`, `required_status_checks`. Copy only the active one — export and import in one pipe, no temp file:

```powershell
gh api repos/tutul032/energy-lakehouse-de/rulesets/25590876 --jq '{name, target, enforcement, conditions, rules, bypass_actors}' | gh api -X POST repos/tutul032/eu-compliance-copilot/rulesets --input -
gh api repos/tutul032/eu-compliance-copilot/rulesets --jq '.[] | [.id, .name, .enforcement] | @tsv'
```

**Expected:** one line `<id>  protect-master  active`. The required check `ci` can be required before it has ever run — the first PR brings the workflow that produces it.

📸 `img/sprint0/03-ruleset.png`

### Step 7 — Open in VS Code on a working branch

```powershell
git switch -c sprint0/scaffold
code .
```

**Expected:** new VS Code window `eu-compliance-copilot`, status bar shows `sprint0/scaffold`, Explorer shows `.gitignore`, `LICENSE`, `README.md`. If VS Code asks whether you trust the folder, choose **Trust** — otherwise extensions such as Claude Code stay disabled.

Copy the two discovery files into the repo root (Claude Code moves them in the next step):

```powershell
Move-Item $HOME\Downloads\discovery.md .
Move-Item $HOME\Downloads\questions.yaml .
```

### Step 8 — Copy-adapt the scaffold with Claude Code

What goes where:

| Copy as-is | Copy and adapt | Write new | Never copy |
| --- | --- | --- | --- |
| `.devcontainer/`, `.claude/settings.json`, lint configs, PR/issue templates | `ci.yml`, `pyproject.toml`, `databricks.yml`, `infra/terraform/` (provider + schemas only), README, CHANGELOG | `src/eucc/`, `eval/`, discovery docs, ADRs | `.git/`, `.venv/`, `.terraform/`, `*.tfstate*`, `tfplan`, `.databrickscfg`, `.env`, Project 1 jobs/data |

Prompt used in Claude Code:

> You are in C:\dev\eu-compliance-copilot on branch `sprint0/scaffold`. Project 1 is at C:\dev\energy-lakehouse-de. It is **read-only**: read it, never modify it. Copy-adapt into this repo:
> 1. Copy as-is: `.devcontainer/`, `.claude/settings.json`, ruff/editorconfig/pre-commit configs, PR and issue templates if they exist.
> 2. Run `uv init --package --name eucc` (src layout), then `uv add pydantic pyyaml` and `uv add --dev pytest ruff`.
> 3. Adapt `.github/workflows/ci.yml`: same pinned action SHAs and runner, job name `ci`, steps uv sync, ruff check, `uv run python -m pytest`. No Databricks auth in CI yet.
> 4. Adapt `databricks.yml`: bundle name `eu-compliance-copilot`, targets dev/staging/prod → schemas `workspace.compliance_dev|staging|prod`, no resources yet.
> 5. `infra/terraform/`: provider and variables from Project 1 plus three `databricks_schema` resources. No service principals, no grants, no state files.
> 6. Stub modules `src/eucc/{ingest,chunk,index,retrieve,agent,mcp_server}.py`; `src/eucc/eval/questions.py` — pydantic loader for `eval/questions.yaml` (unique ids, allowed `expected_behavior`, `expected_sources` present unless tool/refuse/out_of_scope); `tests/test_questions.py`.
> 7. Move my files to `docs/discovery/discovery.md` and `eval/questions.yaml`.
> 8. ADRs `0001-stack.md` and `0002-embedding-model.md` (Proposed) in Project 1's format; `CHANGELOG.md` at 0.0.1; README skeleton.
> 9. Grep the new repo for "energy" and list every hit.
>
> Do not commit, do not push, do not run terraform. Finish with `git status` and the test output.

Verify yourself (PowerShell, repo root):

```powershell
uv run python -m pytest
uv run ruff check .
git grep -n -i "energy"
```

**Expected:** tests pass (40 questions loaded), ruff clean, "energy" only where intended (e.g. the SQL-tool description).

📸 `img/sprint0/04-scaffold-tree.png`

### Step 9 — Plan the infrastructure in the dev container

`Ctrl+Shift+P` → **Dev Containers: Reopen in Container**. A fresh container has no Databricks login and no Terraform variables:

```bash
databricks auth login --host https://dbc-405898bc-c90e.cloud.databricks.com --profile free
export TF_VAR_databricks_host=https://dbc-405898bc-c90e.cloud.databricks.com
export DATABRICKS_CONFIG_PROFILE=free
databricks schemas get workspace.compliance_dev
```

**Expected:** `Profile free was successfully saved`, and `Schema 'workspace.compliance_dev' does not exist` (nothing to import).

```bash
cd /workspaces/eu-compliance-copilot/infra/terraform
terraform init
terraform plan -out=tfplan -input=false
```

**Expected:** `Plan: 3 to add, 0 to change, 0 to destroy.` — `compliance_dev`, `compliance_staging`, `compliance_prod`. Read every resource. **Do not apply on the feature branch.**

Review finding in our run: the plan contained `properties = { "owner" = "root" }`. That attribute was needed in Project 1 to import existing schemas with zero diff; new schemas don't need it. Removed it and re-planned.

Check nothing generated is going into git:

```bash
cd /workspaces/eu-compliance-copilot && git status --short
```

**Expected:** no `tfplan`, `.terraform/`, `*.tfstate`, `.databrickscfg`.

📸 `img/sprint0/05-terraform-plan.png`

### Step 10 — First PR (PowerShell, one line at a time)

```powershell
git add .
git status
git commit -m "chore: sprint 0 scaffold copied-adapted from energy-lakehouse-de"
git push -u origin sprint0/scaffold
gh pr create --fill --base master
git log --oneline origin/master..HEAD
gh pr checks
```

**Expected:** one commit ahead of `origin/master`; `ci  pass`.

```powershell
gh pr merge --squash --delete-branch
git pull
git log --oneline -3
```

**Expected:** `Squashed and merged pull request #1`, and `master` shows the scaffold commit on top of `Initial commit`.

📸 `img/sprint0/06-pr-green.png`

### Step 11 — Apply from master

In the dev container:

```bash
cd /workspaces/eu-compliance-copilot && git pull
cd infra/terraform
terraform plan -out=tfplan -input=false
terraform apply tfplan
databricks schemas list workspace | grep compliance
```

**Expected:**

```
Apply complete! Resources: 3 added, 0 changed, 0 destroyed.
workspace.compliance_dev      rezaul.tutul@yahoo.com
workspace.compliance_prod     rezaul.tutul@yahoo.com
workspace.compliance_staging  rezaul.tutul@yahoo.com
```

The state is local in `infra/terraform/terraform.tfstate` (git-ignored). **Do not delete it** — it moves to Azure Storage in the cloud chapter.

📸 `img/sprint0/07-schemas-created.png`

## Part C — Board and Vector Search ⏳ (to be completed)

### Step 12 — Board and milestones

Create GitHub Project "EUCC" (Board: Backlog / Sprint / In progress / Review / Done), milestones `Sprint 0` … `Sprint 5`, `Hardening`, labels `story`, `spike`, `adr`, `infra`, `eval`.

```powershell
gh api repos/tutul032/eu-compliance-copilot/milestones --jq '.[].title'
```

**Expected:** 7 milestones.

### Step 13 — Vector Search smoke test

In a notebook on serverless: create endpoint `eucc-vs` (STANDARD), a 3-row Delta table with Change Data Feed in `workspace.compliance_dev`, a Delta Sync index with the multilingual embedding endpoint, then a `HYBRID` query in English and German.

**Expected:** endpoint **Online** after ~5–15 min; `gdpr-33-1` ranks first for both "How fast must a data breach be reported?" and "Innerhalb welcher Frist muss eine Datenpanne gemeldet werden?". Record the result in ADR-0002 and set it to Accepted (or switch to per-language indexes if German misses).

### Step 14 — Demo and retro

Record a 5-minute demo (repo, ruleset, green PR, plan/apply, smoke query). Retro: what went well / what to change / one action item.

## Checkpoint — Sprint 0 Definition of Done

- [x] Repo with fresh history, `protect-master` active
- [x] Scaffold merged via PR, `ci` green
- [x] Schemas `compliance_dev|staging|prod` created by Terraform from `master`
- [ ] All 40 questions `reviewed: true`, EUR-Lex attribution recorded
- [ ] Board + 7 milestones
- [ ] Vector Search endpoint Online, smoke result in ADR-0002
- [ ] Demo recording + retro

## Troubleshooting (real problems from this sprint)

| Symptom | Cause | Fix |
| --- | --- | --- |
| `The system cannot find the path specified.` after a `gh api … --jq '… \| …'` command | Terminal was **cmd**, not PowerShell. cmd ignores single quotes and treats `\|` inside the jq filter as a pipe. | Run `powershell` (prompt starts with `PS`); set PowerShell as VS Code default terminal profile. |
| jq filter loses quotes / odd jq errors in PowerShell | Windows PowerShell 5.1 strips embedded `"` when passing arguments to native programs | Use filters without double quotes, e.g. `[.id, .name] \| @tsv` |
| `Problems parsing JSON (HTTP 400)` when posting a ruleset from `ruleset.json` | `>` in PowerShell 5.1 writes **UTF-16**; GitHub expects UTF-8 | Pipe directly: `gh api … \| gh api -X POST … --input -` |
| Two rulesets in the source repo | Earlier attempt left a disabled ruleset | Inspect `enforcement` and `rules` first; copy only the active one. Backlog item: delete the disabled one in Project 1. |
| Claude Code not shown in the new window | New folder in **Restricted Mode** (Workspace Trust), or extension disabled for the workspace | Trust the folder; Extensions → Enable (Workspace); or `Ctrl+Shift+P` → *Claude Code: Open in New Tab*; fallback `claude` in the terminal |
| `No value for required variable "databricks_host"` | New container — env vars from the last container are gone | `export TF_VAR_databricks_host=…` and `export DATABRICKS_CONFIG_PROFILE=free` (later: `remoteEnv` in `devcontainer.json`) |
| `no configuration file found at /home/vscode/.databrickscfg` | New container has no CLI login | `databricks auth login --host … --profile free` |
| `bash: cd: infra/terraform: No such file or directory` | Already inside `infra/terraform` (re-ran a command with a relative `cd`) | Check the prompt path; use absolute paths: `cd /workspaces/eu-compliance-copilot/infra/terraform` |
| Plan shows `properties = { "owner" = "root" }` | Copied from Project 1, where it was needed for zero-diff imports | Remove from the new resource; re-plan |
| `git pull` on master says "Already up to date" right after `gh pr view` shows `MERGEABLE` | `MERGEABLE` only means "no conflicts" — the PR was not merged yet | `gh pr checks` → `gh pr merge --squash --delete-branch`; delete any branch created from the old master |

## What you learned

- How teams reuse a working project: copy-adapt at project 2, template at project 3, shared building blocks at scale — and what must never be copied.
- Discovery artefacts that later drive engineering: success metrics, an evaluation set with expected citations, and a licence check.
- Why the corpus must be versioned (AI Act amended by Regulation 2026/1744).
- Copying GitHub rulesets via the REST API, and the PowerShell traps (cmd vs PowerShell, quote stripping, UTF-16 redirection).
- Infrastructure from reviewed code: plan on the branch for review, merge, re-plan from `master`, apply the saved plan.

## Tested on

2026-11-01 · Windows 11 · Windows PowerShell 5.1 · gh CLI · VS Code + Claude Code · Docker Desktop 4.97 (WSL 2) · dev container with Terraform, Databricks provider v1.139.1 · Databricks Free Edition (serverless)
