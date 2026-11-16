# Azure comparison — same copilot, two platforms

Evaluated 2026-11-16. Azure resources were deleted afterwards to avoid cost; results below are frozen in `eval/results/`. How to recreate: see the end of this page.

## Setup

Everything identical except the search engine and the answer model.

| | Databricks (production) | Azure (comparison) |
|---|---|---|
| Chunks | 3,704 article-level chunks (`gold_chunks`) | the same 3,704 chunks (`data/build/gold_chunks.jsonl`) |
| Search | Databricks Vector Search, hybrid | Azure AI Search (Free tier), hybrid: BM25 + HNSW vectors |
| Embeddings | `databricks-qwen3-embedding-0-6b` | Azure OpenAI `text-embedding-3-small`, 1024 dimensions |
| Answer model | `gpt-oss-120b` (reasoning effort medium) | Azure OpenAI `gpt-4.1-mini` |
| Prompts, JSON contract, citation check, guardrails | identical | identical |
| Questions | 36 (all except data/tool questions), data tool off | same |
| Key-fact judge | Databricks `gpt-oss-120b` | same judge, for fairness |
| Run from | laptop (Essen) | laptop (Essen) |

## Results

| Metric | Databricks | Azure |
|---|---|---|
| Safety (refusals, out-of-scope, false premises) | **1.000** | **1.000** |
| Answer rate | **1.000** | 0.969 (1 miss) |
| Citation hit | **0.909** | **0.909** |
| Key facts (LLM judge, noise ±0.05) | **0.852** | 0.819 |
| p95 latency | 11.6 s | **6.1 s** |
| Errors | 0 | 0 |

## Findings

1. **Retrieval quality is equal on both engines** (citation hit 0.909). The structure-aware chunking with citation headers carries over; the platform is not the deciding factor.
2. **Safety carried over unchanged.** Same prompts, same JSON status contract, same guardrails → 1.000 on both.
3. **Azure answers ~2× faster.** `gpt-4.1-mini` answers directly; `gpt-oss-120b` reasons first. Inside Databricks the release runs measured 6–9 s p95; both values here include the laptop's network path.
4. **Databricks covers slightly more key facts** (0.852 vs 0.819), consistent with the reasoning model writing fuller answers; the difference is within the judge's noise.
5. **The one Azure miss is a search configuration issue.** For *"How does the AI Act define an AI system?"* the rewritten query was "definition of AI system, AI Act". Azure AI Search did not retrieve AI Act Art. 3 (*Definitions*); the answer model then correctly reported that its sources contain no definition (`out_of_scope`). Likely cause: the index uses the **standard analyzer**, which does not stem — "definition" does not match "Definitions" in the keyword half of the hybrid query. A language analyzer (`en.microsoft` / `de.microsoft`) would. Lesson: configuration matters as much as the engine.

## Azure-specific engineering notes

| Topic | What happened | Decision |
|---|---|---|
| Free tier storage (50 MB) | First load: 52.0 MB → quota exceeded on re-upload | Vector field `stored=false` (no second copy of vectors): **21.4 MB**, −59 %, no quality change |
| Space release | Deleting an index frees quota only after a few minutes | Re-run without `--recreate` after the space was released |
| Embedding cost and rate limits | 3,704 chunks ≈ 1.1 M tokens ≈ USD 0.02; several HTTP 429 | Retries with backoff; local embedding cache (re-runs cost nothing) |
| Query vectorization | Index vectorizer failed: the search service cannot authenticate to Azure OpenAI | Query vectors computed client-side with Entra ID; production would use a managed identity (Basic tier or higher) |
| Data-plane permissions | 401 on embeddings although Foundry User was assigned (it was on the project, not the resource) | `Cognitive Services OpenAI User` on the resource; fresh token with `az logout` / `az login` |
| Secrets | Search admin key | Only in a session environment variable, never in the repository |

## Cost

See `docs/tutorial/img/step7/08-cost-analysis.png` (Cost analysis before teardown). Expected order of magnitude: well below USD 1 (embeddings ~USD 0.02, 2×36 chat answers, AI Search Free).

## Recreate

1. Azure free account, resource group, Foundry resource with deployments `gpt-4.1-mini` and `text-embedding-3-small` — `docs/tutorial/step7a-azure-setup.md`
2. `az search service create --name <unique> --resource-group <rg> --sku free --location swedencentral`
3. `uv sync --extra azure`, set `AZURE_SEARCH_ENDPOINT`, `AZURE_SEARCH_KEY`, `AZURE_OPENAI_ENDPOINT`
4. `uv run python -m eucc.backends.azure_index --input data/build/gold_chunks.jsonl --recreate`
5. `uv run python -m eucc.eval.local_eval --backend azure --exclude-category tool --no-data-tool --warmup`
6. `az group delete --name <rg>`
