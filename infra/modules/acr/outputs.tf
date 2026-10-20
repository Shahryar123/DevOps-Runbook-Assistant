output "id" {
  description = "Registry resource ID (scope of the AcrPull role assignments)."
  value       = azurerm_container_registry.this.id
}

output "name" {
  description = "Registry name."
  value       = azurerm_container_registry.this.name
}

output "login_server" {
  description = "Registry host, e.g. acrrbkdevx7k2.azurecr.io."
  value       = azurerm_container_registry.this.login_server
}
