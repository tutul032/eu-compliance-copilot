# Raw volumes, one per schema: hold the raw EUR-Lex/Cellar XHTML written by the extractor (S1-3).
# No grants: the owners (same as the schema's) already have full rights.
resource "databricks_volume" "raw" {
  for_each = databricks_schema.target

  catalog_name = var.catalog
  schema_name  = each.value.name
  name         = "raw"
  volume_type  = "MANAGED"
  owner        = each.value.owner

  lifecycle {
    prevent_destroy = true
  }
}
