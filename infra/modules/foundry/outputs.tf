# The access keys are deliberately not exported (and local auth is off).

output "id" {
  description = "Foundry resource ID (scope of the model-access role assignments)."
  value       = azurerm_cognitive_account.this.id
}

output "name" {
  description = "Foundry resource name."
  value       = azurerm_cognitive_account.this.name
}

output "endpoint" {
  description = "Resource endpoint used as FOUNDRY_ENDPOINT, e.g. https://<name>.cognitiveservices.azure.com/."
  value       = azurerm_cognitive_account.this.endpoint
}

output "project_name" {
  description = "Foundry project name."
  value       = azurerm_cognitive_account_project.this.name
}

output "deployment_names" {
  description = "Names of the model deployments."
  value       = keys(azurerm_cognitive_deployment.this)
}
