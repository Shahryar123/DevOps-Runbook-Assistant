# Outputs: values printed after apply and readable with `terraform output`
# (the scripts use them to find the registry, vault, job, ...).
# None of these is secret. Anything secret would need `sensitive = true`,
# which hides it from CLI output, though it is still stored in state.

output "resource_group_name" {
  description = "Resource group holding everything for this environment."
  value       = azurerm_resource_group.this.name
}

output "app_url" {
  description = "Public HTTPS URL of the Streamlit app (null until deploy_apps = true)."
  value       = one(module.app[*].url)
}

output "app_name" {
  description = "Container App name (for az containerapp commands)."
  value       = one(module.app[*].name)
}

output "acr_name" {
  description = "Container registry name (for az acr build)."
  value       = module.acr.name
}

output "acr_login_server" {
  description = "Registry host the images are pushed to and pulled from."
  value       = module.acr.login_server
}

output "key_vault_name" {
  description = "Key Vault holding the API keys (see key_vault_secrets)."
  value       = module.keyvault.name
}

output "key_vault_secrets" {
  description = "Secrets the containers reference (set-secrets.ps1 prompts for these)."
  value       = sort(keys(local.kv_secrets))
}

output "search_service_name" {
  description = "Azure AI Search service name."
  value       = module.search.name
}

output "search_endpoint" {
  description = "AZURE_SEARCH_ENDPOINT for the app (and for local testing with az login)."
  value       = module.search.endpoint
}

output "search_index_name" {
  description = "Index the ingest job creates."
  value       = var.search_index_name
}

output "job_name" {
  description = "Container Apps Job that runs ingest (null until deploy_apps = true)."
  value       = one(module.ingest_job[*].name)
}

output "app_identity_client_id" {
  description = "Client ID of id-app (AZURE_CLIENT_ID in the app)."
  value       = module.identity.identities["app"].client_id
}

output "job_identity_client_id" {
  description = "Client ID of id-job (AZURE_CLIENT_ID in the job)."
  value       = module.identity.identities["job"].client_id
}

output "ai_resource_group_name" {
  description = "Resource group holding the Foundry resource and its model deployments."
  value       = azurerm_resource_group.ai.name
}

output "foundry_name" {
  description = "Foundry resource name."
  value       = module.foundry.name
}

output "foundry_endpoint" {
  description = "FOUNDRY_ENDPOINT for the app (and for local testing with az login)."
  value       = module.foundry.endpoint
}

output "foundry_project_name" {
  description = "Foundry project (open it at https://ai.azure.com)."
  value       = module.foundry.project_name
}

output "foundry_deployments" {
  description = "Model deployment names (= the model names the app sends)."
  value       = module.foundry.deployment_names
}

output "image" {
  description = "Image deployed to the app and job."
  value       = local.image
}
