# ADR-0007 — Release gates: safety strictly, quality with a margin

Status: Accepted (2026-11-11)

## Context

The release evaluation (`release-eval` workflow, `eval_agent` with `--exclude-category tool --no-data-tool`, 36 questions) gates staging before prod. The first version gated `behavior_correct ≥ 1.0`, `citation_hit ≥ 0.85`, `key_fact_coverage ≥ 0.80` and `errors = 0`.

Three runs of the same system scored differently:

| Metric | Spread (3 runs) | Cause |
|---|---|---|
| citation hit | 0.879–0.909 | Which provisions the answer cites, out of the same sources. |
| key facts | 0.778–0.880 | The LLM judge (and the answer wording it reads). |
| behavior | 0.972–1.000 | One answer question answered in one run, `unsupported` in another. |

Staging run `1017247263344096` showed the same: key facts 0.792 in attempt 0, 0.866 in attempt 1.

A gate at the edge of that spread passes or fails on the draw. That is what happened on staging: attempt 0 failed at 0.792, and the automatic retry passed at 0.866 (fixed in #37: no retries, one eval per job run).

`behavior_correct` mixes two different risks:

- **Safety** (refuse, out_of_scope, correct_premise): giving legal advice, answering about a law outside the corpus, or accepting a false premise. One miss is a defect.
- **Answer rate** (answer, answer+tool): an answer question that ends `unsupported` or `out_of_scope` is a quality loss, and it is visible to the user as "no answer". It is not unsafe.

## Decision

Split `behavior_correct` and gate the two parts differently. `behavior_correct` stays as information in the MLflow run and the `RELEASE_EVAL` line.

| Gate | Threshold | Why |
|---|---|---|
| `safety_correct` | = 1.0 | Strict: no tolerance for a safety miss. 4 questions (adv-01–04). |
| `answer_rate` | ≥ 0.95 | At most 1 of 32 answer questions not answered (31/32 = 0.969 passes, 30/32 = 0.938 fails). Run-to-run variance is one question. |
| `citation_hit` | ≥ 0.85 | Unchanged: 0.03 below the lowest of the three runs (0.879). |
| `key_fact_coverage` | ≥ 0.75, warning < 0.80 | The judge's spread is about 0.07–0.10. 0.75–0.80 is a warning to read, not a failure. |
| `errors` | = 0 | Unchanged, and now strict on real failures only: transient endpoint errors (429, 5xx, timeouts, connection resets) are retried up to 3 times with exponential backoff and jitter in `rag.chat` (router, SQL, answer, judge), and each retry is logged. |

Every question with the wrong behavior or status `error` is printed as `RELEASE_EVAL_MISS id=… expected=… status=… error=…` (the short error message, or `-`), so a passing run with one answer miss still shows which question it was, and an error shows its cause.

## Consequences

- An endpoint hiccup (a 429 burst, a 503 during scale-up) no longer fails a release, since it is retried. An error that survives 4 attempts fails it, with its message in the miss line.
- A safety regression always blocks a release. A single answer-quality fluctuation does not, but it is listed in the log and in the job summary.
- With only 4 safety questions, `safety_correct = 1.0` catches regressions in those 4 cases only. More adversarial questions would strengthen this gate, more than a stricter threshold would.
- The margins are calibrated on a few runs. Revisit them when the question set or the model changes, or after 5–10 release runs show the real spread.
