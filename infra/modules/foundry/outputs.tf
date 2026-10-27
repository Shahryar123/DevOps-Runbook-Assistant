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

output "project_endpoint" {
  description = "Foundry project endpoint used by the evaluation SDK."
  value       = "https://${azurerm_cognitive_account.this.custom_subdomain_name}.services.ai.azure.com/api/projects/${azurerm_cognitive_account_project.this.name}"
}

output "project_principal_id" {
  description = "Object ID of the project's managed identity (runs cloud evaluations)."
  value       = azurerm_cognitive_account_project.this.identity[0].principal_id
}

output "content_filter_name" {
  description = "Custom content filter on the chat deployments."
  value       = azurerm_cognitive_account_rai_policy.this.name
}

output "deployment_names" {
  description = "Names of the model deployments."
  value       = keys(azurerm_cognitive_deployment.this)
}
