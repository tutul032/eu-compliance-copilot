# Workspace-level provider. Only the host is configured here; authentication comes from the
# environment: DATABRICKS_CONFIG_PROFILE=free (OAuth U2M, `databricks auth login`) locally, or
# DATABRICKS_CLIENT_ID / DATABRICKS_CLIENT_SECRET for a service principal.
provider "databricks" {
  host = var.databricks_host
}
