output "name" {
  description = "Container App name."
  value       = azurerm_container_app.this.name
}

output "url" {
  description = "Public HTTPS URL."
  value       = "https://${azurerm_container_app.this.ingress[0].fqdn}"
}

output "latest_revision_name" {
  description = "Name of the newest revision."
  value       = azurerm_container_app.this.latest_revision_name
}
