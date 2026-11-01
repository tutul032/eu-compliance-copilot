# 2. Embedding model: multilingual qwen3-embedding-0-6b

- **Status:** Proposed
- **Date:** 2026-11-01

## Context
Users ask in German or English, and the corpus holds both language versions of each regulation.
A German question must find the right article in either language (target: cross-lingual hit rate
≥ 85 % on the German subset, retrieval recall@5 ≥ 90 %; docs/discovery/discovery.md). The
English-only models available on Free Edition (`gte-large-en`, `bge-large-en`) would match a
German query only against German chunks, and only weakly.

## Decision
- Embed all chunks and queries with **`qwen3-embedding-0-6b`** (multilingual, Apache-2.0; licence
  to be verified on the model card), one index per environment containing EN and DE chunks.
- Validate in Sprint 1 against the German subset of `eval/questions.yaml`.
- **Fallback** if the targets are missed: index DE and EN chunks separately and route each query
  by its detected language.

## Consequences
- One index serves both languages; no language detection is needed on the main path.
- Changing the model later means re-embedding the whole corpus and rebuilding every index, so the
  choice is costly to reverse and recorded here.
- Embedding quality on legal German is unproven until the Sprint 1 eval; the fallback adds a
  routing step and doubles the indexes.
