# The admin and query keys are deliberately not exported. (They are still in
# state as attributes of the resource; see infra/README.md, "What's in state".)

output "id" {
  description = "Search service resource ID (scope of the Search role assignments)."
  value       = azurerm_search_service.this.id
}

output "name" {
  description = "Search service name."
  value       = azurerm_search_service.this.name
}

output "endpoint" {
  description = "Service URL used as AZURE_SEARCH_ENDPOINT."
  value       = "https://${azurerm_search_service.this.name}.search.windows.net"
}
