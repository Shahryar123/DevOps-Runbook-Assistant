# Microsoft Foundry: the models the app calls, hosted in our own subscription.
#
#   Foundry resource (kind = AIServices)   -> endpoint + auth + content filters
#   └── project                            -> workspace for evals/tracing later
#   └── deployments (one per model)        -> what the app actually calls
#
# local_auth_enabled = false -> API keys are refused; callers send an Entra ID
# token and role assignments decide what they may do (same as AI Search).
# The deployment NAME is what the app passes as `model`, so deployments are
# named after their model (gpt-5.4-mini) and config.py needs no extra names.

resource "azurerm_cognitive_account" "this" {
  name                = var.name
  resource_group_name = var.resource_group_name
  location            = var.location
  kind                = "AIServices"
  sku_name            = "S0"

  # Required for token (Entra ID) auth: the endpoint becomes
  # https://<custom_subdomain_name>.cognitiveservices.azure.com/
  custom_subdomain_name = var.name
  local_auth_enabled    = false

  # Makes this a Foundry resource that can hold projects (needs an identity).
  project_management_enabled = true

  identity {
    type = "SystemAssigned"
  }

  tags = var.tags
}

resource "azurerm_cognitive_account_project" "this" {
  name                 = var.project_name
  cognitive_account_id = azurerm_cognitive_account.this.id
  location             = var.location
  display_name         = var.project_name
  description          = "Runbook Assistant models, evals and tracing."

  identity {
    type = "SystemAssigned"
  }

  tags = var.tags
}

resource "azurerm_cognitive_deployment" "this" {
  for_each = var.deployments

  name                 = each.key
  cognitive_account_id = azurerm_cognitive_account.this.id

  model {
    format  = each.value.format
    name    = each.value.model
    version = each.value.version
  }

  sku {
    name     = each.value.sku
    capacity = each.value.capacity # thousands of tokens per minute (quota)
  }

  # Keep the version the deployment was created with; upgrade by setting a
  # version in tfvars, not silently.
  version_upgrade_option = "NoAutoUpgrade"
}
