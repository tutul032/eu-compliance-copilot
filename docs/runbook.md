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
- The workflow does not run the evals. Run `eval_retrieval` (and `eval_rag`/`eval_agent`) on the
  target afterwards to check the new index.
