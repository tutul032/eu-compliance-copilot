# Workspace identities. OAuth secrets are never managed here, so they never reach the state (see
# docs/adr/0003-identity-and-ownership.md); group membership is not managed either.
resource "databricks_service_principal" "cicd" {
  display_name               = "sp-eucc-cicd"
  workspace_access           = true
  databricks_sql_access      = true
  allow_cluster_create       = false
  allow_instance_pool_create = false

  lifecycle {
    prevent_destroy = true
    # The workspace also grants the workspace-consume entitlement, which the provider refuses
    # together with workspace_access; keep it as it is instead of removing it.
    ignore_changes = [workspace_consume]
  }
}
