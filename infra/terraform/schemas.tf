# Schemas: one per bundle target. The bundle never creates them. All owned by the platform
# owner for now; no service principals and no grants yet.
locals {
  # Set by Unity Catalog on every schema of this workspace; declared so the plan has no diff.
  schema_properties = { owner = "root" }

  target_schemas = {
    dev     = "compliance_dev"
    staging = "compliance_staging"
    prod    = "compliance_prod"
  }
}

resource "databricks_schema" "target" {
  for_each = local.target_schemas

  catalog_name = var.catalog
  name         = each.value
  owner        = var.platform_owner
  properties   = local.schema_properties

  lifecycle {
    prevent_destroy = true
  }
}
