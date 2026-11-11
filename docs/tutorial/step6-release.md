# Step 6 — Staging via CI, release gate, v1.0.0

Project 3 · EU Compliance Copilot · Tutorial 07

> Goes to: `docs/tutorial/step6-release.md` · Decision: `docs/adr/0007-release-gates.md`

## Goal

- Build the staging corpus and index **entirely in CI**, as the service principal — no laptop.
- A release evaluation on staging that **fails** when quality or safety drops — and prove that it fails.
- README, model card with risk log, tag `v1.0.0`.

## Concepts

**The owner creates.** Staging and prod schemas belong to `sp-eucc-cicd`; your user cannot create volumes there. The SP creates its own volumes through a bundle deployment it runs in CI.

**`databricks_permissions` is authoritative.** Unlike `databricks_grant`, it replaces an object's whole ACL. Use it only on objects the project owns alone (the Vector Search endpoint), never on shared ones (the SQL warehouse used by Project 1).

**A gate is only proven when it has failed once.** Run it with an impossible threshold and expect red.

**Measure variance before setting thresholds.** The same system evaluated several times varies; hard gates on noisy metrics fail randomly and get ignored.

## Step 1 — Staging corpus via CI

Claude Code: bundle volume resource `raw` for staging/prod; workflow `refresh-corpus.yml` (`workflow_dispatch`): uv sync → `bundle deploy -t <target>` → extractor with `--upload-to /Volumes/workspace/compliance_<target>/raw/eurlex` → `bundle run build_corpus -t <target>`.

Merge first — a `workflow_dispatch` workflow only runs from the default branch:

```
HTTP 404: workflow refresh-corpus.yml not found on the default branch
```

```powershell
gh workflow run refresh-corpus.yml -R tutul032/eu-compliance-copilot -f target=staging
gh run watch <id> -R tutul032/eu-compliance-copilot
```

**First run:** deploy ✓, extract and upload ✓, `build_corpus` ✗:

```
PermissionDenied: [PERMISSION_DENIED] … assign the user *** 'Can Use' or 'Can Manage' for the endpoint 'eucc-vs'.
Task index failed …
```

Fix via Terraform (`infra/terraform/vector_search.tf`): `databricks_permissions` on the endpoint, `CAN_USE` for the CI SP. Plan: `1 to add`. Merge, apply from master, re-run.

**Second run:** all green in 19 min (first index sync in staging). Check:

```powershell
databricks vector-search-indexes list-indexes eucc-vs -p free -o json | ConvertFrom-Json | Select-Object name, creator
```

**Expected:** `compliance_dev.chunks_idx` (you) and `compliance_staging.chunks_idx` (`0af2526a-…`).

## Step 2 — Release evaluation workflow

Claude Code: agent option `use_data_tool=False`; `eval_agent` options `--exclude-category tool --no-data-tool --variants medium --warmup` + thresholds; workflow `release-eval.yml` with the `RELEASE_EVAL` line in the job summary. The data questions stay in the full dev evaluation (the CI SP has no access to the shared warehouse).

```powershell
gh workflow run release-eval.yml -R tutul032/eu-compliance-copilot -f target=staging
```

**First run: green — but wrong:**

```
RELEASE_EVAL behavior=1.000 citation_hit=0.909 key_facts=0.792 p95_ms=9592 errors=0 n=36
RELEASE_EVAL FAILED: key_facts=0.792 < min-key-facts 0.8
UserWarning: To exit: use 'exit', 'quit', or Ctrl-D.
RELEASE_EVAL behavior=1.000 citation_hit=0.909 key_facts=0.866 p95_ms=8846 errors=0 n=36
```

Two bugs: `sys.exit(1)` is swallowed by the IPython shell of the Databricks task (the task succeeds), and the eval ran twice. Fix: raise an exception; one eval per run; workflow safety net on `RELEASE_EVAL FAILED`.

## Step 3 — Prove the gate fails

```powershell
git log --oneline -1 origin/master
gh workflow run release-eval.yml -R tutul032/eu-compliance-copilot -f target=staging -f min_citation_hit=0.99
```

**Actual: red** — `RELEASE_EVAL FAILED: citation_hit=0.909 < min-citation-hit 0.99`.

Always check `origin/master` before `gh workflow run`: what is on master is what runs.

## Step 4 — Measure variance, split the gate

Three runs of the same system:

| Run | behavior | citation hit | key facts | p95 | errors |
|---|---|---|---|---|---|
| 1 | 1.000 | 0.909 | 0.880 | 7.7 s | 0 |
| 2 | 0.972 | 0.879 | 0.778 | 7.3 s | 0 |
| 3 | 0.972 | 0.879 | 0.819 | 6.2 s | 1 |

Gate design (ADR-0007):

- `safety_correct` (refuse / out_of_scope / correct_premise) = **1.0** — hard
- `answer_rate` (answer questions answered) ≥ **0.95**
- citation hit ≥ **0.85**; key facts ≥ **0.75** (warning below 0.80); errors = **0**
- transient LLM errors retried with backoff; `RELEASE_EVAL_MISS` line per missed question

**Final run: green:**

```
RELEASE_EVAL_MISS id=gdpr-06 expected=answer status=unsupported error=-
RELEASE_EVAL safety=1.000 answer_rate=0.969 behavior=0.972 citation_hit=0.879 key_facts=0.778 p95_ms=8801 errors=0 n=36
```

gdpr-06: the grounding guardrail replaced an answer without citation markers — over-cautious, not unsafe (risk log R5).

## Step 5 — README, model card, release

Add `README.md`, `docs/model-card.md`, this tutorial, the app screenshot (`docs/tutorial/img/step5/02-app-deployed.png`) and `CHANGELOG.md` (1.0.0).

```powershell
git switch master
git pull
git switch -c release/v1.0.0
git add -A
git status --short
git commit -m "docs: README, model card and risk log, tutorial 07; changelog 1.0.0"
git push -u origin release/v1.0.0
gh pr create --fill --base master
gh pr checks --watch
gh pr merge --squash --delete-branch
git switch master
git pull
gh release list -R tutul032/eu-compliance-copilot
gh release create v1.0.0 -R tutul032/eu-compliance-copilot --target master --title "v1.0.0 — EU Compliance Copilot" --notes-file docs/release-notes-v1.0.0.md
```

**Expected:** no earlier release listed; release `v1.0.0` created on master.

## Checkpoint

- [x] Staging corpus + index built by CI as the SP
- [x] Release gate proven red, then green
- [x] README, model card with risk log, tutorials 00–07
- [x] `v1.0.0`

## Troubleshooting (real problems from this step)

| Symptom | Cause | Fix |
| --- | --- | --- |
| `HTTP 404: workflow … not found on the default branch` | `workflow_dispatch` workflows run from `master` only | Merge the workflow first |
| `PERMISSION_DENIED … 'Can Use' … endpoint 'eucc-vs'` | Endpoint owned by your user | `databricks_permissions` CAN_USE for the SP (project-owned object only) |
| Workflow green although `RELEASE_EVAL FAILED` | `sys.exit` swallowed by IPython in the Databricks task | Raise an exception; workflow safety net |
| Two `RELEASE_EVAL` lines with different numbers | Eval executed twice per run | One eval per run, `max_retries: 0` |
| A run used old gates | Fix not merged; `gh workflow run` uses master | `git log --oneline -1 origin/master` before running |
| `? Select a workflow` menu | `gh` command without workflow or run id | Always pass the name or id |
| Behavior 0.972 in one run, 1.000 in another | Non-deterministic answers; hard gate on 36 answers flaps | Split safety (hard) from answer rate (margin) |
| `errors=1` | Transient endpoint errors | Retry 429/5xx/timeouts with backoff |
| Deploy waits in a second run | Bundle deployment lock | Run release evaluations one at a time |

## What you learned

- Letting CI own staging: identities, ownership, and the one permission it still needed.
- Testing a quality gate by making it fail.
- Setting thresholds from measured variance; separating safety gates from quality gates.

## Tested on

2026-11-10 – 2026-11-11 · GitHub Actions (ubuntu runner, uv, Databricks CLI setup action) · Databricks Free Edition · Terraform Databricks provider
