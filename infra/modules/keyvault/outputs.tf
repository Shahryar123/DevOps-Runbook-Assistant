output "id" {
  description = "Vault resource ID (scope of the Key Vault role assignments)."
  value       = azurerm_key_vault.this.id
}

output "name" {
  description = "Vault name."
  value       = azurerm_key_vault.this.name
}

output "vault_uri" {
  description = "Vault URI with a trailing slash, e.g. https://kv-rbk-dev-x7k2.vault.azure.net/."
  value       = azurerm_key_vault.this.vault_uri
}
