# Platform grants, one databricks_grant per securable and principal: authoritative only for that
# principal on that securable, so grants made elsewhere stay untouched. The catalog is shared with
# Project 1's Terraform state, so databricks_grants (plural, authoritative for every principal on
# a securable) must never be used here: it would revoke Project 1's grants. Not managed here (see
# docs/adr/0003-identity-and-ownership.md): the _workspace_users catalog grant, Project 1's
# grants, table grants (except the app service principal's read grants in app.tf), bundle
# resources.
resource "databricks_grant" "catalog_use" {
  catalog    = var.catalog
  principal  = databricks_service_principal.cicd.application_id
  privileges = ["USE_CATALOG"]
}

# Read access for the platform owner to the schemas owned by the CI/CD SP.
resource "databricks_grant" "target_schema_owner_read" {
  for_each = toset(["staging", "prod"])

  schema     = databricks_schema.target[each.key].id
  principal  = var.platform_owner
  privileges = ["SELECT", "USE_SCHEMA"]
}
