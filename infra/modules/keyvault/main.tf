# Key Vault: holds the API keys (openai-api-key, langsmith-api-key). Terraform
# creates only the vault; the secret values are written by
# scripts/set-secrets.ps1, so they never appear in .tf files, plans or state.
#
# RBAC authorization: access is granted with Azure role assignments (Key
# Vault Secrets User / Officer) instead of the older per-vault access
# policies, so all permissions are managed the same way.

resource "azurerm_key_vault" "this" {
  name                       = var.name
  resource_group_name        = var.resource_group_name
  location                   = var.location
  tenant_id                  = var.tenant_id
  sku_name                   = "standard"
  rbac_authorization_enabled = true
  purge_protection_enabled   = var.purge_protection_enabled
  soft_delete_retention_days = var.soft_delete_retention_days

  # Public endpoint, protected by Entra ID + RBAC. Container Apps on the
  # Consumption plan without a VNet has no fixed outbound IPs to allow-list
  # (see .trivyignore.yaml).
  public_network_access_enabled = true

  tags = var.tags
}
