# Step 7a — Azure account, cost guardrails and model deployments

Project 3 · EU Compliance Copilot · Tutorial 08

> Goes to: `docs/tutorial/step7a-azure-setup.md` · Screenshots: `docs/tutorial/img/step7/`

## Goal

Prepare an Azure subscription for porting the copilot's retrieval and generation to Azure AI Search and Azure OpenAI:

- a free account with its own directory and subscription,
- a budget with alerts before any resource exists,
- a resource group and a Microsoft Foundry resource + project in an EU region,
- two model deployments: a chat model and an embedding model,
- the data-plane role you need to use them.

**Result:** `gpt-4.1-mini` and `text-embedding-3-small` deployed (Global Standard, Sweden Central), budget $20/month with alerts, no quota problems.

## Concepts

**Directory (tenant) and subscription.** A Microsoft Entra directory holds identities; a subscription holds resources and is billed. A personal Microsoft account has neither for Azure until you sign up — the portal then fails with a token error.

**Free account limits.** USD 200 credit for 30 days plus selected free services for 12 months. The free account has a **spending limit**: when the credit ends, resources stop instead of being charged. Upgrading to pay-as-you-go removes that protection — don't upgrade for a portfolio project.

**Guardrails first.** A budget with alerts is created before the first resource. It doesn't stop spending; it warns you early.

**Control plane vs data plane.** Owning a subscription lets you create and delete resources (control plane). Using a model or creating agents inside a Foundry project needs an extra role (data plane: *Foundry User*, formerly *Azure AI User*) — owners don't get it automatically.

**Deployment, not model.** In Foundry you don't call "a model"; you call a **deployment** with a name you choose. The code later uses the deployment name.

**Temporary by design.** These Azure resources exist for a comparison and are destroyed afterwards. The repository keeps the code, the infrastructure definition and the frozen results (ADR-0008).

## Step 1 — Sign in and read the first error correctly

Open https://portal.azure.com with your Microsoft account.

**Actual (first sign-in, account never used for Azure):**

```
Interaction required
… AADSTS16000: User account '{EUII Hidden}' from identity provider 'live.com' does not exist in tenant
'Microsoft Services' and cannot access the application … (Azure Portal) in that tenant.
```

Under your name the portal shows the directory id `F8CDEF31-A31E-4B4A-93E4-5F57…` — that is Microsoft's shared directory for personal accounts, not an Azure directory of your own. Nothing is broken: you have no Azure directory and no subscription yet. Click **Ignore**.

## Step 2 — Create the free account

https://azure.microsoft.com/free → **Start free**, sign in with the **same** account, then:

1. Personal details (individual, Germany)
2. Phone verification (SMS code)
3. Card verification (temporary hold, released; nothing charged within the free credit)
4. Accept the agreement

**Actual:** email "Welcome to Azure … You now have access to 12 months of free services and a USD200 credit."

Sign out of the portal completely and sign in again.

**Expected:** no error; under your name a **Default Directory** (here `rezaultutulyahoo.onmicrosoft.com`); **Subscriptions** shows *Azure subscription 1*, status Active; **Cost Management + Billing → Billing scopes** shows your billing account (Microsoft Customer Agreement), role *Billing account owner*, status Active.

The 30-day credit starts now — note the end date under **Cost Management + Billing → Credits + commitments**.

📸 `img/step7/01-billing-scope.png`

## Step 3 — Secure the account

Avatar → **View account / Security info** → add the **Microsoft Authenticator** app (MFA).

## Step 4 — Budget with alerts (before any resource)

**Cost Management → Budgets → + Add**:

- Amount: 20 (USD in the free account)
- Reset period: Monthly
- Alerts: 50 %, 80 %, 100 % (Actual) → your email

**Actual:** budget `azuresubscription1`, Monthly, $20.00.

Check it really alerts: open the budget → **Alert conditions** → three rows with your email.

Note: in our run the budget was created at the **billing-account** scope (scope column showed a different GUID than the subscription id). That covers all spending, but add the same budget under **Subscriptions → Azure subscription 1 → Budgets** as well, so it is attached directly to where the resources run.

📸 `img/step7/02-budget.png`

## Step 5 — Azure CLI and resource group

```powershell
winget install -e --id Microsoft.AzureCLI
```

New PowerShell window:

```powershell
az login
az account show --query "{name:name, state:state, id:id}" -o table
az group create --name rg-eucc-azure --location swedencentral
```

**Expected:** `Azure subscription 1  Enabled  96ed17df-…`; the resource group with `"provisioningState": "Succeeded"`.

Region choice: Sweden Central is an EU region that offers the Azure OpenAI chat and embedding models used here. Availability changes — confirm it in the model catalog (Step 7) before building.

## Step 6 — Foundry resource and project

Portal → search **Foundry** → **Create** → resource group `rg-eucc-azure`, region **Sweden Central**, default names.

**Actual:** resource `rg-eucc-azurep3` (kind *AIServices*), location swedencentral, status Succeeded; project `rg-eucc-azure-proj3`.

Naming lesson: the `rg-` prefix is for resource groups; a clearer name for the Foundry resource would be `ai-eucc-swc`. Not worth recreating now — but follow the convention next time.

📸 `img/step7/03-foundry-resource.png`

## Step 7 — Deploy the models

On the resource page click **Go to Foundry portal** (opens the project in the **Microsoft Foundry** portal, "New Foundry" toggle on).

**Models → Deploy → Deploy base model**:

| Deployment name | Model | Type |
|---|---|---|
| `gpt-4.1-mini` | gpt-4.1-mini (2025-04-14) | Global Standard |
| `text-embedding-3-small` | text-embedding-3-small (1) | Global Standard |

**Actual:** both listed under **Models → Deployments → Serverless**, status *Succeeded*.

Test the chat model in its **Playground**: type "Hello" → a reply. The embedding model has **no playground** — it returns vectors, not text; it is tested by code when the chunks are embedded.

If a deployment fails with insufficient quota / quota 0, switch between *Global Standard* and *Standard*, or try another EU region, before building anything.

📸 `img/step7/04-deployments.png`

## Step 8 — Give yourself the data-plane role

The playground shows:

```
You don't have permission to create agents in this project. To create agents, ask your administrator
to assign you the Foundry User role.
```

You are the administrator. Portal → Foundry resource `rg-eucc-azurep3` → **Access control (IAM)** → **+ Add → Add role assignment** → **Foundry User** (in some views still **Azure AI User**) → **Members → + Select members** → your account → **Review + assign**.

**Expected:** after 2–5 minutes and a page reload, the banner is gone. The role is also needed when code calls the models with your Azure login (Entra ID) instead of an API key.

## Checkpoint

- [x] Own directory and free subscription; credit end date noted
- [x] MFA on
- [x] Budget $20/month with 3 alerts
- [x] Resource group `rg-eucc-azure` (Sweden Central)
- [x] Foundry resource + project
- [x] `gpt-4.1-mini` and `text-embedding-3-small` deployed, Global Standard
- [x] Foundry User role assigned

## Troubleshooting (real problems from this step)

| Symptom | Cause | Fix |
| --- | --- | --- |
| `AADSTS16000 … does not exist in tenant 'Microsoft Services'` on first portal sign-in | Personal account without an Azure directory or subscription | Sign up for the free account with the same login, then sign out and in again |
| Budget scope GUID differs from the subscription id | Budget created at billing-account scope | Fine for total spend; add a subscription-level budget too |
| "Apps now has a new home" / unfamiliar menus | Portal and Foundry UIs change often | Use search; the concepts (resource, project, deployment) stay the same |
| No playground for the embedding model | Embeddings return vectors, not chat | Test via code when embedding the chunks |
| "You don't have permission to create agents … Foundry User role" | Owner role is control plane only | Assign yourself Foundry User / Azure AI User on the Foundry resource |

## What you learned

- Directory vs subscription, and how to read an Entra sign-in error.
- Cost guardrails on a free account: spending limit, budget, alerts — before the first resource.
- Foundry structure: resource → project → deployments; deployment names are what code calls.
- Control-plane vs data-plane permissions in Azure.

## Tested on

2026-11-15 · Windows 11 · Azure portal · Microsoft Foundry portal (New Foundry) · Azure CLI · region Sweden Central
