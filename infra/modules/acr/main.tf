# Azure Container Registry: stores the runbook-assistant image built by
# scripts/build-image.ps1. The app and job pull it with their managed
# identities (AcrPull), so the admin user (a shared username/password) stays
# disabled.

resource "azurerm_container_registry" "this" {
  name                = var.name
  resource_group_name = var.resource_group_name
  location            = var.location
  sku                 = "Basic"
  admin_enabled       = false
  tags                = var.tags
}
