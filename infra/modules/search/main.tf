# Azure AI Search: the vector (and keyword) index the app queries. Terraform
# creates only the service; the index itself is created by the ingest job
# (AzureSearch creates it on first use, with our schema).
#
# local_authentication_enabled = false -> API keys are refused; callers must
# send an Entra ID token, and role assignments decide what they may do
# (RBAC is supported on every tier, including free). The fallback variable
# re-enables keys (and Entra ID) if RBAC ever has to be bypassed.

resource "azurerm_search_service" "this" {
  name                = var.name
  resource_group_name = var.resource_group_name
  location            = var.location
  sku                 = var.sku

  local_authentication_enabled = var.local_auth_enabled
  # Only valid while keys are enabled: accept both keys and Entra ID tokens.
  authentication_failure_mode = var.local_auth_enabled ? "http401WithBearerChallenge" : null

  # Free tier: no replica/partition settings, no semantic ranker.
  tags = var.tags
}
