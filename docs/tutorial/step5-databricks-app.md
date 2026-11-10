# Step 5 — Databricks App: the copilot as a web app

Project 3 · EU Compliance Copilot · Tutorial 06

> Goes to: `docs/tutorial/step5-databricks-app.md`

## Goal

A Streamlit chat app on Databricks Apps that anyone with the link (and workspace access) can use:

- DE/EN interface, example questions, answers with status badge,
- EUR-Lex citation links with article anchors, data rows as a table, SQL in an expander,
- disclaimer, warehouse warm-up,
- running as **its own identity** with least-privilege grants managed by Terraform.

**Result:** answers in 3–8 s when warm (GDPR 5.4 s, price 3.1 s, mixed question 7.9 s).

## Concepts

**An app has its own service principal.** Databricks creates one per app (here `app-4q05z5 eucc-copilot`, client id `49323a2c-…`). The app runs as that identity, not as you — everything it reads must be granted to it. Locally the same app runs as you, which is why "works locally" proves nothing about permissions.

**Two kinds of permissions.** Workspace objects (model endpoints, SQL warehouse) are declared as app `resources` in the bundle and granted on deploy. Unity Catalog objects (tables, index, volumes, schemas) are grants — managed in Terraform, singular `databricks_grant` (ADR-0003).

**Own wheels only from a volume.** The Apps runtime installs dependencies with uv and runs a security scan; your own wheel must be referenced from `/Volumes/...`, not shipped inside the app folder.

**Cold starts.** The app compute, the model endpoint and the serverless warehouse all start on demand. The first answer after idle is slow; warm answers are fast. The app warms the warehouse on load.

## Step 1 — Branch

```powershell
cd C:\dev\eu-compliance-copilot
git switch master
git pull
git switch -c step5/app
```

## Step 2 — Build the app (Claude Code)

> Read `src/eucc/agent.py`, the CLI module, `databricks.yml`, `resources/`, `docs/adr/0006-agent-design.md`.
> 1. `app/app.py` (Streamlit), `app/app.yaml` (`command: ["streamlit", "run", "app.py"]`): DE/EN switch (UI texts + example questions; answer language detected from the question), 4 example questions per language, chat input, status badge, answer, citations as links (localized header as link text), data rows as a dataframe with 2-decimal formatting, expander with SQL and regulation query, route and latency, permanent disclaimer, chat history in `st.session_state`.
> 2. Warehouse warm-up (`SELECT 1` in a background thread) on first load, with a hint while it runs.
> 3. Config via env vars in `app.yaml`; MLflow tracing off in the app (`EUCC_TRACING=0`).
> 4. `resources/eucc_app.app.yml`: app `eucc-copilot`, `source_code_path: ../app`, app resources: serving endpoints `databricks-gpt-oss-120b`, `databricks-qwen3-embedding-0-6b` (CAN_QUERY), SQL warehouse `4228ff3a910b0b5e` (CAN_USE).
> 5. Local run with profile `free`; unit tests for UI helpers.

## Step 3 — Run locally

```powershell
$env:DATABRICKS_CONFIG_PROFILE = "free"
uv run streamlit run app/app.py
```

**Expected:** `http://localhost:8501`; the load-forecast example shows Annex III with a link, the MAPE table and the SQL. Locally answers take 40–50 s (network from the laptop + cold endpoints).

📸 `img/step5/01-app-local.png`

## Step 4 — First deploy (fails, by design of the platform)

```powershell
databricks bundle deploy -t dev -p free
databricks bundle run eucc_app -t dev -p free
```

**Actual:**

```
✓ App is started!
✓ Error installing packages. Please check /logz for more details
Error: failed to reach SUCCEEDED, got FAILED
```

Read the log:

```powershell
databricks apps logs eucc-copilot -p free
```

**Actual:**

```
[BUILD] [INFO] Installing dependencies with uv...
[BUILD] [ERROR] uv-requirements.txt may only contain clean wheel paths under /Volumes/, found: ./wheels/eucc-0.0.1-py3-none-any.whl
```

The app exists anyway — get its identity:

```powershell
databricks apps get eucc-copilot -p free -o json | ConvertFrom-Json | Select-Object name, service_principal_client_id, service_principal_name
```

**Actual:** `eucc-copilot  49323a2c-3b24-411e-9d06-f7d24b2e8864  app-4q05z5 eucc-copilot`

## Step 5 — Volume and grants via Terraform

Claude Code:

> `infra/terraform/app.tf`: variable `app_sp_client_id` (default `49323a2c-…`); MANAGED volume `artifacts` in `compliance_dev`; `databricks_grant` (singular) for the app SP: `USE_CATALOG` workspace, `USE_SCHEMA` compliance_dev, `READ_VOLUME` artifacts, `SELECT` on `gold_chunks` and `chunks_idx`, `USE_SCHEMA` energy_prod, `SELECT` on the 7 catalog tables. `app/uv-requirements.txt` → `/Volumes/workspace/compliance_dev/artifacts/eucc-0.0.1-py3-none-any.whl`; remove `app/wheels/`; script `scripts/publish_app_wheel.ps1` (`uv build --wheel`, `databricks fs cp --overwrite`).

Plan in the **dev container**, from the Terraform folder:

```bash
databricks current-user me --profile free > /dev/null && echo "auth ok"
cd /workspaces/eu-compliance-copilot/infra/terraform
terraform plan -input=false
```

**First plan (ours):** `16 to add` — 14 expected (volume + 13 grants) plus **`databricks_volume.raw["staging"]` and `raw["prod"]`**. Check:

```bash
for s in dev staging prod; do echo "== $s"; databricks volumes list workspace compliance_$s -p free; done
```

**Actual:** `raw` exists in dev only. In step 1-3 the staging/prod creations had failed (schemas owned by `sp-eucc-cicd`; the user cannot create volumes there) and nobody noticed. Fix: `raw` for `["dev"]` only, staging/prod in step 6 by the CI identity. Re-plan:

**Expected:** `Plan: 14 to add, 0 to change, 0 to destroy.` — every grant names principal `49323a2c-…`.

PR, merge, then apply from master in the container:

```bash
cd /workspaces/eu-compliance-copilot/infra/terraform
git pull
terraform plan -out=tfplan -input=false
terraform apply tfplan
```

**Expected:** `Apply complete! Resources: 14 added, 0 changed, 0 destroyed.` with no `Error:` lines above it.

## Step 6 — Publish the wheel and deploy

```powershell
powershell -ExecutionPolicy Bypass -File scripts\publish_app_wheel.ps1
databricks bundle deploy -t dev -p free
databricks bundle run eucc_app -t dev -p free
```

**Actual:**

```
Successfully built dist\eucc-0.0.1-py3-none-any.whl
Uploaded dist\eucc-0.0.1-py3-none-any.whl to dbfs:/Volumes/workspace/compliance_dev/artifacts/eucc-0.0.1-py3-none-any.whl
Updated apps.eucc_app
✓ App is started!
```

## Step 7 — Test in the browser

Open the URL from `databricks apps get eucc-copilot -p free`.

**Actual (warm):**

| Question | Result | Time |
|---|---|---|
| How quickly must a personal data breach be notified …? | GDPR Art. 33 with link | 5.4 s |
| Load forecast high-risk + current error | Annex III; MAE 2,656 MWh, MAPE 4.96 % (vs baseline 4.08 %, issued 9 Nov) | 7.9 s (27 s cold) |
| What was the average DE-LU day-ahead price yesterday? | 345.64 EUR/MWh for 9 Nov | 3.1 s |
| Was muss ein Finanzunternehmen nach DORA nach einem schwerwiegenden IKT-bezogenen Vorfall tun? | German answer, DORA Art. 19, 17, 11, 13 | 7.2 s |

Verify the data numbers against the table before a demo:

```sql
SELECT date_local, price_avg_eur_mwh FROM workspace.energy_prod.gold_energy_daily ORDER BY date_local DESC LIMIT 3;
```

Note the forecast result: on 9 Nov the model was **worse than the naive baseline** — the app shows it as it is.

📸 `img/step5/02-app-deployed.png`

## Step 8 — Polish and close

Claude Code:

> In `app/app.py`: make the disclaimer visible permanently (footer or sidebar): "Not legal advice. Sources: EUR-Lex — AI Act consolidated 27.07.2026, GDPR consolidated 04.05.2016, DORA OJ L 333/2022. Energy data: Project 1 (energy_prod)." In the answer prompt: cite only as `[n]`; mention paragraphs in the text (e.g. "Art. 19(3)"), never `[n]§3`.

```powershell
git switch -c step5/app-polish
git add -A
git status --short
git commit -m "feat(app): permanent disclaimer, citation format; tutorial 06"
git push -u origin step5/app-polish
gh pr create --fill --base master
gh pr checks --watch
gh pr merge --squash --delete-branch
git switch master
git pull
powershell -ExecutionPolicy Bypass -File scripts\publish_app_wheel.ps1
databricks bundle deploy -t dev -p free
databricks bundle run eucc_app -t dev -p free
```

Free Edition: one app; app compute stops after inactivity / 24 h — start it with `databricks bundle run eucc_app -t dev -p free` before a demo and ask one data question to warm the warehouse.

## Checkpoint

- [x] App deployed from the bundle, running as its own SP
- [x] 14 Terraform resources: artifacts volume + least-privilege grants
- [x] Wheel installed from a UC volume
- [x] EN and DE answers, citations with anchors, data table, SQL expander
- [x] Disclaimer visible

## Troubleshooting (real problems from this step)

| Symptom | Cause | Fix |
| --- | --- | --- |
| `Error installing packages`, log: `uv-requirements.txt may only contain clean wheel paths under /Volumes/` | Apps only install own wheels from a UC volume | Upload the wheel to `/Volumes/.../artifacts/`, reference that path; grant `READ_VOLUME` to the app SP |
| Works locally, fails deployed | Local = your permissions; deployed = the app's SP | Grant the app SP what it reads (Terraform) |
| Plan wants to create `raw` in staging/prod | Earlier partial apply failure went unnoticed | Check all environments; scope to dev; SP creates its own volumes |
| `Error: No configuration files` | `terraform` run outside `infra/terraform` | `cd /workspaces/eu-compliance-copilot/infra/terraform` first |
| `The token '&&' is not a valid statement separator` | Bash command in Windows PowerShell 5.1 | Run Terraform/bash commands in the dev container; PowerShell equivalent `cmd; if ($?) { … }` |
| Dev container hangs, log `read ECONNRESET` while downloading VS Code Server | VS Code updated; server download for WSL interrupted | Wait for retry, or download the server tarball manually into the `serverCache` folder from the log |
| "Apps now has a new home" in Compute → Apps | UI moved to the app switcher | Use the switcher or the CLI (`databricks apps get/logs`) |
| First answer 27 s or more, later 3–8 s | Cold app compute, model endpoint, warehouse | Warm up before demos |
| Citations rendered as `[1]§3` | Model appends paragraphs to citation markers | Prompt rule: cite `[n]` only, paragraphs in the text |

## What you learned

- Deploying a Streamlit app with Databricks Asset Bundles.
- App identities, and the split between bundle-declared resources and Unity Catalog grants.
- Why "works locally" hides permission problems, and how to read app build logs.
- Finding an old partial failure from an unexpected plan — and checking every environment after an apply.

## Tested on

2026-11-10 · Windows 11 · Windows PowerShell 5.1 · VS Code 1.145.2 + Dev Containers 0.469.0 · Databricks Apps (Streamlit) · Terraform Databricks provider · Databricks Free Edition
