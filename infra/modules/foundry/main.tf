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

# --- Content filter ("guardrail" in the Foundry portal) ------------------------
# Runs inside Foundry on every request to a chat deployment that uses it, on
# the prompt and on the completion. Without it, deployments get the built-in
# Microsoft.DefaultV2 policy; declaring our own makes the settings visible
# and reviewable in code, and lists it under Guardrails in the portal.
#
# Harm categories block at severity_threshold and above (Low = strictest).
# Jailbreak = Prompt Shields for user prompts; Indirect Attack = Prompt
# Shields for documents (only sees text the app marks as documents, so it
# only annotates here); Protected Material = known copyrighted text / code.

locals {
  harm_filters = [
    for pair in setproduct(["Hate", "Sexual", "Violence", "Selfharm"], ["Prompt", "Completion"]) : {
      name               = pair[0]
      source             = pair[1]
      block              = true
      severity_threshold = var.content_filter.severity_threshold
    }
  ]
  special_filters = [
    { name = "Jailbreak", source = "Prompt", block = true, severity_threshold = null },
    { name = "Indirect Attack", source = "Prompt", block = false, severity_threshold = null },
    { name = "Protected Material Text", source = "Completion", block = true, severity_threshold = null },
    { name = "Protected Material Code", source = "Completion", block = false, severity_threshold = null },
  ]
}

resource "azurerm_cognitive_account_rai_policy" "this" {
  name                 = var.content_filter.name
  cognitive_account_id = azurerm_cognitive_account.this.id
  base_policy_name     = "Microsoft.DefaultV2"

  dynamic "content_filter" {
    for_each = concat(local.harm_filters, local.special_filters)
    content {
      name               = content_filter.value.name
      source             = content_filter.value.source
      filter_enabled     = true
      block_enabled      = content_filter.value.block
      severity_threshold = content_filter.value.severity_threshold
    }
  }

  tags = var.tags
}

resource "azurerm_cognitive_deployment" "this" {
  for_each = var.deployments

  name                 = each.key
  cognitive_account_id = azurerm_cognitive_account.this.id
  # Chat models only; embedding deployments don't take a content filter.
  rai_policy_name = each.value.content_filter ? azurerm_cognitive_account_rai_policy.this.name : null

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
