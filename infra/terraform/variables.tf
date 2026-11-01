variable "databricks_host" {
  description = "Workspace URL (same as workspace.host in databricks.yml)."
  type        = string
}

variable "catalog" {
  description = "Unity Catalog catalog that holds all schemas (Free Edition: workspace)."
  type        = string
  default     = "workspace"
}

variable "platform_owner" {
  description = "User who owns the compliance_* schemas."
  type        = string
  default     = "rezaul.tutul@yahoo.com"
}
