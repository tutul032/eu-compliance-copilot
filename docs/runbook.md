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
