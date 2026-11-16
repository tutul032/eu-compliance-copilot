# Changelog

All notable changes, by GitHub release. Versions follow [semver](https://semver.org/); dates are
release dates (UTC).

## [1.1.0] - 2026-11-16

### Added
- Backend interface (`retrieve`, `fetch_article`, `chat`); `AzureBackend` on Azure AI Search (hybrid) + Azure OpenAI `gpt-4.1-mini` with Entra ID auth.
- `eucc.backends.azure_index` loader (vector field `stored=false`, embedding cache), `eucc.eval.local_eval --backend`, `eucc.ask --backend`.
- Side-by-side evaluation (`eval/results/*-2026-11-16.*`), `docs/azure-comparison.md`, ADR-0008, tutorials 08–09.

### Notes
- Azure resources deleted after evaluation; CI does not depend on Azure.

## [1.0.0] - 2026-11-12

### Added
- Corpus manifest `corpus/manifest.yaml`: 12 entries, 6 EUR-Lex sources in EN and DE (GDPR and
  AI Act consolidated plus original for recitals, DORA original, Digital Omnibus on AI original).
- Its validator `eucc.corpus.manifest` (unique ids, every source in both languages, URL matches
  CELEX and language), tested in `tests/test_manifest.py`.
- Spike `docs/spikes/s1-2-eurlex-format.md`: ingest HTML and parse the ELI structure, not PDF.

## [0.0.1] - Sprint 0 scaffold

### Added
- Python package `eucc` (uv, src layout) with stub modules `ingest`, `chunk`, `index`,
  `retrieve`, `agent`, `mcp_server`.
- Evaluation question set `eval/questions.yaml` (40 items) and its validator
  `eucc.eval.questions` (unique ids, allowed `expected_behavior`, `expected_sources` unless
  `tool`/`refuse`/`out_of_scope`), tested in `tests/test_questions.py`.
- Databricks Asset Bundle `eu-compliance-copilot` with targets dev/staging/prod mapped to
  `workspace.compliance_dev|staging|prod`; no resources yet.
- Terraform (`infra/terraform/`) for the three schemas; separate state, no service principals or
  grants.
- GitHub Actions `ci.yml` (ruff, pytest), dev container, pre-commit hook, Claude Code settings.
- Discovery document (`docs/discovery/discovery.md`); ADR 0001 (stack) and ADR 0002 (embedding
  model), both Proposed.
