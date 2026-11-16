# ADR-0008 — Azure as a comparison backend, not an environment

Status: Accepted (2026-11-16)

## Context

Target roles in NRW ask for Databricks **and** Azure. The copilot runs on Databricks (dev/staging/prod). An Azure version should show portability and allow a like-for-like comparison, within a 30-day free credit — the Azure resources will not stay alive.

## Decision

- Azure is a **second backend** behind a common interface (`retrieve`, `fetch_article`, `chat`), not a fourth environment. Databricks stays the default and the only deployed system.
- Same chunks, prompts, JSON contract, guardrails and question set; the key-fact judge stays on Databricks for both backends.
- Azure resources (resource group `rg-eucc-azure`: Foundry resource with `gpt-4.1-mini` and `text-embedding-3-small`, AI Search Free) are created by CLI, evaluated, documented and **deleted** (`az group delete`). Terraform `azurerm` is used in the Project 1 Azure chapter instead.
- Results are committed (`eval/results/*.csv|json`, `docs/azure-comparison.md`) because the resources disappear.
- CI does not depend on Azure: Azure code is tested with mocks; no Azure secrets in GitHub.
- Authentication: Azure OpenAI via Entra ID (`Cognitive Services OpenAI User` on the resource); AI Search via admin key in a session environment variable only. Production would use managed identities (requires AI Search Basic or higher).

## Consequences

- The repository stays clean after teardown; anyone can recreate the comparison with their own account.
- Findings recorded in `docs/azure-comparison.md`: equal citation hit (0.909), equal safety (1.000), Azure ~2× faster, one Azure retrieval miss caused by the non-stemming standard analyzer.
- Not done (backlog): language analyzers per language field, semantic ranker, Foundry agent service, managed identity on a paid tier.
