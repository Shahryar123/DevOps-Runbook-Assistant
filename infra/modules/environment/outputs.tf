output "id" {
  description = "Environment resource ID (where the app and job run)."
  value       = azurerm_container_app_environment.this.id
}

output "default_domain" {
  description = "Default DNS suffix of apps in this environment."
  value       = azurerm_container_app_environment.this.default_domain
}
