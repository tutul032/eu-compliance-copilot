# Step 7b — Azure AI Search index, Azure backend and side-by-side evaluation

Project 3 · EU Compliance Copilot · Tutorial 09

> Goes to: `docs/tutorial/step7b-azure-search-eval.md` · Results: `docs/azure-comparison.md` · Decision: `docs/adr/0008-azure-backend.md`

## Goal

- Load the same 3,704 chunks into Azure AI Search (Free tier).
- Run the copilot with Azure AI Search + Azure OpenAI behind the same interface.
- Evaluate both platforms on the same 36 questions and record the result.
- Tear the Azure resources down and release v1.1.0.

**Result:** citation hit 0.909 on both, safety 1.000 on both, Azure ~2× faster, one Azure retrieval miss explained.

## Concepts

**Same chunks, different engine.** Reusing `data/build/gold_chunks.jsonl` makes the comparison fair: only search engine and answer model change.

**Hybrid search in Azure AI Search.** One query runs BM25 over `chunk_text` and HNSW over `chunk_vector`; results are fused.

**Stored vs searchable vectors.** A vector field can be searchable without being stored for retrieval. Not storing it removes a full second copy of the vectors.

**Who embeds the query.** An index *vectorizer* lets the service embed query text — but then the search service itself must authenticate to Azure OpenAI (API key or managed identity). Embedding the query in the client avoids storing a secret in the service.

**Data plane needs its own role.** Calling Azure OpenAI with your Entra login needs `Cognitive Services OpenAI User` on the resource.

## Step 1 — Branch and AI Search service

```powershell
git switch master
git pull
git switch azure/ai-search      # or: git switch -c azure/ai-search
git branch --show-current
az login
az search service create --name srch-eucc-tutul --resource-group rg-eucc-azure --sku free --location swedencentral
```

**Expected:** `"sku": {"name": "free"}`, `"status": "running"`. Name must be globally unique; one Free service per subscription.

Session variables (never in a file):

```powershell
$env:AZURE_SEARCH_ENDPOINT = "https://srch-eucc-tutul.search.windows.net"
$env:AZURE_SEARCH_KEY = az search admin-key show --service-name srch-eucc-tutul --resource-group rg-eucc-azure --query primaryKey -o tsv
$env:AZURE_OPENAI_ENDPOINT = az cognitiveservices account show --name rg-eucc-azurep3 --resource-group rg-eucc-azure --query properties.endpoint -o tsv
"key length: " + $env:AZURE_SEARCH_KEY.Length
```

**Expected:** `key length: 52`. Variables live only in this PowerShell window.

## Step 2 — Index loader (Claude Code)

> `azure` optional extra (`azure-search-documents`, `azure-identity`, `openai`). `src/eucc/backends/azure_index.py`: index `eucc-chunks` (key `chunk_id`; filterable `reg`, `lang`, `unit_type`, `article`, `paragraph`, `celex`, `version_date`; `chunk_text` searchable; `chunk_vector` 1024-d HNSW cosine, `stored=False`); embeddings with `text-embedding-3-small` (`dimensions=1024`, batches of 64, retries on 429/5xx, Entra auth via `DefaultAzureCredential`); embedding cache `data/azure/embeddings.jsonl`; `merge_or_upload` in batches of 500; poll stats until the count matches; warn above 45 MB. No vectorizer on the index. Unit tests without network.

## Step 3 — Load the index (what really happened)

```powershell
uv sync --extra azure
uv run python -m eucc.backends.azure_index --input data/build/gold_chunks.jsonl --recreate
```

**Run 1:** `missing environment variables …` — new PowerShell window. Set the variables again.

**Run 2:**

```
… embeddings … "HTTP/1.1 401 PermissionDenied"
The principal `40836ea3-…` lacks the required data action
`Microsoft.CognitiveServices/accounts/OpenAI/deployments/embeddings/action`
```

Check your roles:

```powershell
$me = az ad signed-in-user show --query id -o tsv
az role assignment list --assignee $me --all --query "[].{role:roleDefinitionName, scope:scope}" -o table
```

**Actual:** `Foundry User` only on `…/accounts/rg-eucc-azurep3/projects/rg-eucc-azure-proj3` — the project, not the resource. Assign the API role on the resource:

```powershell
$scope = az cognitiveservices account show --name rg-eucc-azurep3 --resource-group rg-eucc-azure --query id -o tsv
az role assignment create --assignee-object-id $me --assignee-principal-type User --role "Cognitive Services OpenAI User" --scope $scope
```

**Run 3** (4 minutes later): `401 Principal does not have access to API/Operation`. Fix: fresh token.

```powershell
az logout
az login
```

**Run 4:** embedded 3704/3704, uploaded 3704, but the stats line showed `0 documents` — statistics lag. Check:

```powershell
$s = Invoke-RestMethod -Uri "$env:AZURE_SEARCH_ENDPOINT/indexes/eucc-chunks/stats?api-version=2024-07-01" -Headers @{ "api-key" = $env:AZURE_SEARCH_KEY }
"docs: {0}  storage: {1:N1} MB  vector: {2:N1} MB" -f $s.documentCount, ($s.storageSize/1MB), ($s.vectorIndexSize/1MB)
```

**Actual:** `docs: 3704  storage: 52.0 MB  vector: 35.3 MB` — above the Free tier's 50 MB.

**Fix:** vector field `stored=False` + embedding cache. **Run 5** with `--recreate`: embedding fine (several 429 retried), upload failed:

```
Storage quota has been exceeded for this service.
```

Service statistics showed `storage: 0.0 / 50.0 MB` a minute later — the deleted index's space is released with a delay. **Run 6** without `--recreate` (cache hit, no embedding calls):

```
index eucc-chunks: 3704 documents, storage 21.4 MB, vector index 14.7 MB
```

−59 % storage, same search quality.

## Step 4 — Search explorer

Portal → `srch-eucc-tutul` → Indexes → `eucc-chunks` → Search explorer, query `data breach notification 72 hours`.

**First try:** `Could not complete vectorization action. The service failed to authenticate to the vectorization endpoint.` — the index vectorizer runs as the search service, which has no credentials for Azure OpenAI (a managed identity needs Basic tier or higher). Decision: remove the vectorizer, embed queries client-side; in the explorer switch **Query options → Vector search → Off**.

**Expected:** keyword results (`"@odata.count": 700` in our run), GDPR Art. 33 chunks near the top.

📸 `img/step7/05-search-explorer.png`, `img/step7/06-search-index.png`

## Step 5 — Azure backend and local evaluation (Claude Code)

> `src/eucc/backends/base.py` interface (`retrieve`, `fetch_article`, `chat`); `DatabricksBackend` (unchanged behavior) and `AzureBackend` (hybrid `search_text` + `VectorizedQuery`, OData filters for lang/unit_type/reg, chat via `gpt-4.1-mini`, JSON response format, Entra auth, retries). `rag.py`/`agent.py` take a backend. `eucc.eval.local_eval --backend databricks|azure --exclude-category tool --no-data-tool --warmup [--only id]`: release-eval metrics, judge always Databricks gpt-oss, results to `eval/results/`, column `retrieved_chunk_ids`. CLI `eucc.ask --backend azure`. Mocked unit tests.

```powershell
$env:DATABRICKS_CONFIG_PROFILE = "free"
uv run python -m eucc.ask --backend azure "How quickly must a personal data breach be notified to the supervisory authority?"
uv run python -m eucc.eval.local_eval --backend databricks --exclude-category tool --no-data-tool --warmup
uv run python -m eucc.eval.local_eval --backend azure --exclude-category tool --no-data-tool --warmup
```

**Actual:**

```
RELEASE_EVAL safety=1.000 answer_rate=1.000 behavior=1.000 citation_hit=0.909 key_facts=0.852 p95_ms=11591 errors=0 n=36
RELEASE_EVAL_MISS id=aia-01 expected=answer status=out_of_scope error=-
RELEASE_EVAL safety=1.000 answer_rate=0.969 behavior=0.972 citation_hit=0.909 key_facts=0.819 p95_ms=6132 errors=0 n=36
```

📸 `img/step7/07-azure-answer.png`

## Step 6 — Explain the one miss

```powershell
uv run python -m eucc.eval.local_eval --backend azure --exclude-category tool --no-data-tool --only aia-01
Import-Csv eval\results\azure-2026-11-16-2.csv | Select-Object status, regulation_query, retrieved_chunk_ids | Format-List
Remove-Item eval\results\azure-2026-11-16-2.*
```

**Actual:** `regulation_query: definition of AI system, AI Act`; retrieved Art. 5, 2, 9, 6, 4, 80, Annex III — **no Art. 3**. Retrieval miss, reproducible. Likely cause: the standard analyzer does not stem "definition" ↔ "Definitions". The answer model correctly reported that its sources contain no definition.

## Step 7 — Document, tear down, release

1. `docs/azure-comparison.md`, `docs/adr/0008-azure-backend.md`, this tutorial, README status line:
   > Databricks backend: live (dev/staging). Azure backend: evaluated Nov 2026 on the same 36 questions, resources deleted afterwards — results in `docs/azure-comparison.md`.
2. 📸 Cost Management → Cost analysis → `img/step7/08-cost-analysis.png`
3. Commit, PR, merge.
4. Teardown:

```powershell
az group delete --name rg-eucc-azure --yes
az group exists --name rg-eucc-azure
```

**Expected:** `false`. Budget and account stay (no cost).

5. Release:

```powershell
gh release create v1.1.0 -R tutul032/eu-compliance-copilot --target master --title "v1.1.0 — Azure comparison backend" --notes "Azure AI Search + Azure OpenAI backend behind the same interface; side-by-side evaluation on 36 questions (docs/azure-comparison.md). Azure resources deleted after evaluation."
```

## Checkpoint

- [x] 3,704 chunks in Azure AI Search Free (21.4 MB)
- [x] Azure backend behind the common interface; Databricks unchanged
- [x] Side-by-side evaluation committed
- [x] One miss explained
- [x] Resources deleted; v1.1.0

## Troubleshooting (real problems from this step)

| Symptom | Cause | Fix |
| --- | --- | --- |
| `missing environment variables` | New PowerShell window | Set the session variables again (or a helper script that fetches them via `az`) |
| 401 `lacks the required data action …/embeddings/action` | Foundry User only on the project | `Cognitive Services OpenAI User` on the Foundry resource |
| 401 `Principal does not have access to API/Operation` after the assignment | Old token | `az logout` + `az login` |
| Stats show 0 documents right after upload | Statistics update with delay | Poll / check again; `docs/$count` endpoint |
| Storage 52 MB on Free tier (50 MB) | Vectors stored twice | `stored=False` on the vector field → 21.4 MB |
| `Storage quota has been exceeded` right after `--recreate` | Deleted index's space released with delay | Wait, re-run without `--recreate` |
| Many HTTP 429 during embedding | Low default deployment capacity | Retries with backoff; embedding cache |
| `Could not complete vectorization action` in Search explorer | Vectorizer needs credentials for Azure OpenAI | Client-side query embedding; vector search off in the explorer |
| `git commit` → "On branch master … no changes added" | Branch existed, `git switch -c` failed, no switch followed | `git switch <branch>`, `git add -A`, then commit |
| `The '<' operator is reserved` | Placeholder `<column name>` typed literally | Replace placeholders with real values |
| Azure answers `out_of_scope` for the AI-system definition | Art. 3 not retrieved; standard analyzer doesn't stem | Documented; language analyzers in backlog |

## What you learned

- Building an Azure AI Search index from your own chunks, within Free-tier limits.
- Data-plane roles and token refresh in Azure.
- Storage-optimized vector fields; why a vectorizer needs its own identity.
- Running two platforms behind one interface and comparing them fairly — and explaining a difference from evidence.
- Temporary cloud resources: document, measure cost, delete.

## Tested on

2026-11-16 · Windows 11 · Azure CLI · Azure AI Search (Free) · Azure OpenAI (`gpt-4.1-mini`, `text-embedding-3-small`, Global Standard, Sweden Central) · azure-search-documents 12.1.0 · Databricks Free Edition
