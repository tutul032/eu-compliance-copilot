# Changelog

All notable changes, by GitHub release. Versions follow [semver](https://semver.org/); dates are
release dates (UTC).

## [Unreleased]

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
