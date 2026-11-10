# The Vector Search endpoint eucc-vs: CAN_USE for the CI/CD SP, so that build_corpus (run as
# sp-eucc-cicd in staging and prod) can create and sync compliance_<target>.chunks_idx on it.
# The endpoint itself was created outside Terraform and is referenced by its id
# (`databricks vector-search-indexes list-indexes eucc-vs`), not managed here.
#
# databricks_permissions is authoritative for this object's ACL: every direct permission on
# eucc-vs not listed here is removed on apply. That is acceptable because the endpoint is used by
# Project 3 only; its owner and the workspace admins keep access, since owner and admin rights are
# not part of the managed ACL. Grant any further principal here, never in the UI.
resource "databricks_permissions" "vector_search_endpoint" {
  vector_search_endpoint_id = "37e58d36-522a-45c0-8bbf-f5a8509a509f"

  access_control {
    service_principal_name = databricks_service_principal.cicd.application_id
    permission_level       = "CAN_USE"
  }
}
