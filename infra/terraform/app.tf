# The Databricks App eucc-copilot (resources/eucc_app.app.yml, dev target): the volume its eucc
# wheel is installed from, and the app service principal's read grants. One databricks_grant per
# securable and principal: authoritative only for the app SP on that securable, so grants made
# elsewhere (Project 1's on energy_prod, the bundle's app resources) stay untouched; never
# databricks_grants (plural). The app SP itself is created by Databricks Apps with the app, so it
# is referenced by its client id, not managed here. Not managed here: the app's CAN_QUERY on the
# serving endpoints and CAN_USE on the SQL warehouse (app resources in the bundle).
variable "app_sp_client_id" {
  description = "Client id (application id) of the app's service principal (`databricks apps get eucc-copilot`)."
  type        = string
  default     = "49323a2c-3b24-411e-9d06-f7d24b2e8864"
}

locals {
  app_schema = databricks_schema.target["dev"].name

  # The regulation search reads the index (a Unity Catalog table of type
  # TABLE_ONLINE_VECTOR_INDEX_*, so it takes a table grant) and its source table.
  app_compliance_tables = ["gold_chunks", "chunks_idx"]

  # Project 1's gold tables the SQL tool may query: src/eucc/tools/energy_catalog.yaml.
  energy_schema = "energy_prod"
  app_energy_tables = [
    "gold_energy_hourly",
    "gold_energy_daily",
    "gold_forecast_accuracy",
    "gold_forecast_hourly",
    "gold_dq_daily_summary",
    "gold_dq_anomalies",
    "gold_dq_daily_summary_text",
  ]
}

# The eucc wheel the app installs (app/uv-requirements.txt), uploaded by
# scripts/publish_app_wheel.ps1.
resource "databricks_volume" "artifacts" {
  catalog_name = var.catalog
  schema_name  = local.app_schema
  name         = "artifacts"
  volume_type  = "MANAGED"
  owner        = var.platform_owner

  lifecycle {
    prevent_destroy = true
  }
}

resource "databricks_grant" "app_catalog_use" {
  catalog    = var.catalog
  principal  = var.app_sp_client_id
  privileges = ["USE_CATALOG"]
}

resource "databricks_grant" "app_schema_use" {
  schema     = databricks_schema.target["dev"].id
  principal  = var.app_sp_client_id
  privileges = ["USE_SCHEMA"]
}

resource "databricks_grant" "app_artifacts_read" {
  volume     = databricks_volume.artifacts.id
  principal  = var.app_sp_client_id
  privileges = ["READ_VOLUME"]
}

resource "databricks_grant" "app_compliance_select" {
  for_each = toset(local.app_compliance_tables)

  table      = "${var.catalog}.${local.app_schema}.${each.key}"
  principal  = var.app_sp_client_id
  privileges = ["SELECT"]
}

resource "databricks_grant" "app_energy_schema_use" {
  schema     = "${var.catalog}.${local.energy_schema}"
  principal  = var.app_sp_client_id
  privileges = ["USE_SCHEMA"]
}

resource "databricks_grant" "app_energy_select" {
  for_each = toset(local.app_energy_tables)

  table      = "${var.catalog}.${local.energy_schema}.${each.key}"
  principal  = var.app_sp_client_id
  privileges = ["SELECT"]
}
