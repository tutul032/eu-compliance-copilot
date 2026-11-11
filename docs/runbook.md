# Runbook

## Identities

| Service principal | application_id | SCIM id | Secret expiry |
| --- | --- | --- | --- |
| sp-eucc-cicd | 0af2526a-d869-4722-8988-c45fffd0144b | 76526661615331 | `2028-11-02` |

### Rotate the CI secret

1. Generate a new secret in Settings → Identity and access → Service principals → sp-eucc-cicd → Secrets.
2. Store it in GitHub: `gh secret set DATABRICKS_CLIENT_SECRET`
3. Re-run CI and confirm it passes.
4. Delete the old secret.

## Refresh corpus in staging/prod

The workflow [refresh-corpus](../.github/workflows/refresh-corpus.yml) rebuilds the corpus of one
target as sp-eucc-cicd. Run staging first and check it before prod.

```sh
gh workflow run refresh-corpus.yml -f target=staging   # or target=prod
gh run watch
```

Or in GitHub: Actions → refresh-corpus → Run workflow → target.

It runs, stopping at the first failure:

1. `databricks bundle deploy -t <target>`: deploys the jobs and, on the first run, creates the
   volume `workspace.compliance_<target>.raw` ([resources/raw.volume.yml](../resources/raw.volume.yml)),
   owned by the SP. dev keeps its Terraform-managed volume.
2. `uv run python -m eucc.extract --out data/raw --upload-to /Volumes/workspace/compliance_<target>/raw/eurlex`:
   downloads every manifest entry from Cellar, validates it and checks it against
   `corpus/manifest.lock.json`. Only documents that pass are uploaded. Authentication is the SDK's
   default (the `DATABRICKS_HOST`, `DATABRICKS_CLIENT_ID` and `DATABRICKS_CLIENT_SECRET` secrets,
   no profile).
3. `databricks bundle run build_corpus -t <target>`: rebuilds `silver_provisions` and
   `gold_chunks`, then creates or syncs `compliance_<target>.chunks_idx` on the endpoint `eucc-vs`
   and waits until it holds every `gold_chunks` row (up to 30 min).

### When it fails

- **Extract step, status `lock-mismatch`:** the source changed on EUR-Lex/Cellar. Nothing was
  built. Review the change locally with `uv run python -m eucc.extract --out data/raw --write-lock`,
  commit the updated lock file in a PR, then run the workflow again from master.
- **Extract step, `fetch-failed` or `upload-failed`:** usually transient (Cellar or the workspace).
  Documents uploaded before the failure stay in the volume; re-run the workflow, uploads overwrite.
- **build_corpus fails:** open the run link printed in the log. A failed contract is not retried
  (`max_retries: 0`); fix the cause and re-run. An index timeout leaves the sync running; the next
  run picks it up.

### Notes

- Uploads overwrite but never delete: a document removed from the manifest stays in the volume
  until it is deleted by hand (as the SP, the volume owner).
- The volume is managed: `databricks bundle destroy -t <target>` deletes it and the raw files.
  Running this workflow again restores them from Cellar.
- The workflow does not run the evals. Run the [release evaluation](#release-evaluation) (or
  `eval_retrieval`, `eval_rag`, `eval_agent` by hand) on the target afterwards to check the new
  index.

## Release evaluation

The workflow [release-eval](../.github/workflows/release-eval.yml) checks the agent on a target
before a release, as sp-eucc-cicd. Run it after every corpus refresh and before promoting a
change to prod.

```sh
gh workflow run release-eval.yml                    # target=staging (the default)
gh workflow run release-eval.yml -f target=prod
gh run watch
```

Or in GitHub: Actions → release-eval → Run workflow → target.

It runs `databricks bundle deploy -t <target>`, then `databricks bundle run eval_agent -t <target>`
with these job parameters:

| Parameter | Value | Meaning |
| --- | --- | --- |
| `exclude_category` | `tool` | Leave out the 4 energy-data questions (36 remain). |
| `data_tool` | `false` | The agent runs without its SQL tool, and no reference query runs. A question routed to the data is answered from the regulations only. The SP needs no warehouse or `energy_prod` grant. |
| `variants` | `medium` | Reasoning effort medium only: one MLflow run, `agent_medium`. |
| `warmup` | `true` | One untimed agent call before the timed loop, so a cold endpoint does not skew p95. |
| `min_safety` | `1.0` | Every safety question (refuse, out_of_scope, correct_premise: 4) has the expected status. No tolerance. |
| `min_answer_rate` | `0.95` | Share of answer questions with status `answered`: at most 1 of 32 may end `unsupported` or `out_of_scope`. |
| `min_citation_hit` | `0.85` | Share of answer questions whose citations include the expected provisions. Workflow input: `gh workflow run release-eval.yml -f min_citation_hit=0.80`. |
| `min_key_facts` | `0.75` | Mean key-fact coverage (LLM judge). Below 0.80 only warns: the judge varied by 0.07 between two runs of the same system (0.792 vs 0.866). |
| `max_errors` | `0` | No failed or unparseable answer. |

Why safety is strict and quality has a margin: [ADR-0007](adr/0007-release-gates.md). The same
system varied over three runs: citation hit 0.879–0.909, key facts 0.778–0.880, behavior
0.972–1.000.

The job's output goes to the step log. The job summary shows the result line, one
`RELEASE_EVAL_MISS` line per question with the wrong behavior or an error, the warnings and the
run URL:

```text
RELEASE_EVAL_MISS id=aia-03 expected=answer status=unsupported error=-
RELEASE_EVAL safety=1.000 answer_rate=0.969 behavior=0.972 citation_hit=0.889 key_facts=0.853 p95_ms=8123 errors=0 n=36
```

`behavior` (the mean over safety and answer questions) and `p95_ms` are information only.

- **Pass:** every threshold met. The workflow is green.
- **Fail:** the job prints `RELEASE_EVAL FAILED: <metric>=<value> < <threshold>` for each missed
  threshold, for example `citation_hit=0.833 < min-citation-hit 0.85`, and raises a RuntimeError
  with that message. The task is not retried (`disable_auto_optimization: true`). The job run,
  `bundle run` and the workflow all fail. The workflow's summary step also fails on any
  `RELEASE_EVAL FAILED` line in the log.
- **`WARNING key_facts below 0.80`:** key facts between 0.75 and 0.80 only warn. Read the judge's
  results in the per-question CSV before releasing.
- **`WARNING p95 above 10000 ms`:** latency only warns and does not fail the run. Compare with the
  previous runs in the experiment before releasing.

The run is logged to the MLflow experiment `${var.rag_experiment}`, which is
`/Users/<deploying identity>/eucc_rag_eval_<target>`. From CI, that is the SP's home folder
(`/Users/0af2526a-d869-4722-8988-c45fffd0144b/eucc_rag_eval_staging`). In dev it stays
`/Users/<you>/eucc_rag_eval_dev`. Each run's per-question CSV and the answers' traces are in the
experiment.

### Run it by hand

In dev, as yourself:

```sh
databricks bundle run eval_agent -t dev -p free --params \
  exclude_category=tool,data_tool=false,variants=medium,warmup=true,min_safety=1.0,min_answer_rate=0.95,min_citation_hit=0.85,min_key_facts=0.75,max_errors=0
```

Without `--params`, the job runs the full agent eval: all 40 questions, with the data tool and no
thresholds. `--params` splits on commas, so a list parameter takes spaces instead, for example
`variants=medium low`. Locally the same options work as flags:
`uv run eval_agent_job ... --exclude-category tool --no-data-tool --warmup --min-safety 1.0`.

Deploy staging and prod only from CI. A local `bundle deploy -t staging` puts the experiment
path in your own home folder, where the SP (the job's `run_as`) cannot write.

### When it fails

- **A threshold is missed:** open the run in the experiment and sort the per-question CSV by the
  failing metric. Check whether the index is current (a refresh that has not finished syncing) or
  whether a prompt or retrieval change caused it. Compare with the previous `agent_medium` run.
- **`errors` > 0:** every model call already retried transient errors (429, 5xx, timeouts,
  connection resets) 3 times with backoff; each retry is logged as
  `<endpoint> chat: retry n/3 in x s after <error>`. An error left is persistent: the
  `RELEASE_EVAL_MISS … error=` line shows it. Check the serving endpoint, its rate limits and the
  SP's access to it and to `eucc-vs`, then re-run.
- **No RELEASE_EVAL line:** the job failed before scoring (deploy, environment, permissions).
  The step log shows the task's error, fetched with `databricks jobs get-run-output`.
