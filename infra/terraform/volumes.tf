# Raw volumes, one per schema: hold the raw EUR-Lex/Cellar XHTML written by the extractor (S1-3).
# No grants: the owners (same as the schema's) already have full rights.
# staging/prod raw volumes are created in step 6 by sp-eucc-cicd (schema owner); the user identity
# cannot create volumes in SP-owned schemas.
resource "databricks_volume" "raw" {
  for_each = toset(["dev"])

  catalog_name = var.catalog
  schema_name  = databricks_schema.target[each.key].name
  name         = "raw"
  volume_type  = "MANAGED"
  owner        = databricks_schema.target[each.key].owner

  lifecycle {
    prevent_destroy = true
  }
}
