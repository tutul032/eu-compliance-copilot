# Schemas: one per bundle target. The bundle never creates them; staging/prod are owned by the
# CI/CD SP (see docs/adr/0003-identity-and-ownership.md), dev by the platform owner.
locals {
  # Set by Unity Catalog on every schema of this workspace; declared so the plan has no diff.
  schema_properties = { owner = "root" }

  target_schemas = {
    dev     = { name = "compliance_dev", owner = var.platform_owner }
    staging = { name = "compliance_staging", owner = databricks_service_principal.cicd.application_id }
    prod    = { name = "compliance_prod", owner = databricks_service_principal.cicd.application_id }
  }
}

resource "databricks_schema" "target" {
  for_each = local.target_schemas

  catalog_name = var.catalog
  name         = each.value.name
  owner        = each.value.owner
  properties   = local.schema_properties

  lifecycle {
    prevent_destroy = true
  }
}
