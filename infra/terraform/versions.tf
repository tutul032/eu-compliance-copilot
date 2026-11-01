# Terraform and provider versions. CI (ci.yml job `terraform`) and the dev container install the
# same Terraform version; .terraform.lock.hcl pins the provider hashes.
terraform {
  required_version = "~> 1.16.6"

  required_providers {
    databricks = {
      source  = "databricks/databricks"
      version = "1.139.1"
    }
  }
}
