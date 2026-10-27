# Locals: values computed once from variables and reused below (names, tags,
# env vars). Unlike variables they can't be set from outside, and unlike
# outputs they aren't shown after apply.

# A short random suffix makes globally unique names (Key Vault, ACR, Search
# are DNS names shared by every Azure customer). It's stored in state, so it
# stays the same on every later plan.
resource "random_string" "suffix" {
  length  = 4
  lower   = true
  upper   = false
  numeric = true
  special = false
}

data "azurerm_client_config" "current" {}

locals {
  base   = "${var.prefix}-${var.environment}" # e.g. rbk-dev
  suffix = random_string.suffix.result        # e.g. x7k2
  unique = "${local.base}-${local.suffix}"    # e.g. rbk-dev-x7k2

  names = {
    resource_group = "rg-${local.base}"
    log_analytics  = "log-${local.unique}"
    acr            = "acr${var.prefix}${var.environment}${local.suffix}" # letters/digits only
    key_vault      = "kv-${local.unique}"                                # max 24 chars
    search         = "srch-${local.unique}"
    environment    = "cae-${local.unique}"
    app            = "ca-${local.base}-app"
    job            = "caj-${local.base}-ingest"
    id_app         = "id-${local.base}-app"
    id_job         = "id-${local.base}-job"
    budget         = "budget-${local.base}"

    resource_group_ai = "rg-${local.base}-ai"
    foundry           = "aif-${local.unique}" # also the endpoint's DNS name
    foundry_project   = "proj-${local.base}"
    budget_ai         = "budget-${local.base}-ai"
  }

  foundry_location = coalesce(var.foundry_location, var.location)

  tags = merge(var.extra_tags, {
    app         = "runbook-assistant"
    environment = var.environment
    owner       = var.owner
    managedBy   = "terraform"
  })

  # The human operator: an explicit object ID, or whoever runs Terraform.
  owner_object_id = coalesce(var.owner_object_id, data.azurerm_client_config.current.object_id)

  image = var.image_tag == null ? null : "${module.acr.login_server}/${var.image_repository}:${var.image_tag}"

  # Key Vault secret name -> environment variable inside the containers.
  # The values are set with scripts/set-secrets.ps1, never in Terraform.
  kv_secrets = merge(
    {
      "openai-api-key"    = "OPENAI_API_KEY"
      "langsmith-api-key" = "LANGSMITH_API_KEY"
    },
    var.search_local_auth_enabled ? { "search-admin-key" = "AZURE_SEARCH_API_KEY" } : {},
  )

  # Version-less secret IDs: the app always gets the latest version.
  container_secrets = {
    for name, env_name in local.kv_secrets : name => {
      env_name            = env_name
      key_vault_secret_id = "${module.keyvault.vault_uri}secrets/${name}"
    }
  }

  # Plain (non-secret) settings, read by config.py from the environment.
  common_env = {
    VECTOR_STORE            = "azure_search"
    RETRIEVAL_MODE          = var.retrieval_mode
    AZURE_SEARCH_ENDPOINT   = module.search.endpoint
    AZURE_SEARCH_INDEX_NAME = var.search_index_name
    LANGSMITH_TRACING       = "true"
    LANGSMITH_PROJECT       = var.langsmith_project
    # Chat + embeddings from Foundry with the managed identity (no key).
    # Deployment names match the model names in config.py.
    LLM_PROVIDER     = var.llm_provider
    FOUNDRY_ENDPOINT = module.foundry.endpoint
  }

  app_env = merge(local.common_env, {
    # DefaultAzureCredential uses this to pick the user-assigned identity.
    AZURE_CLIENT_ID = module.identity.identities["app"].client_id
    # Container Apps ingress is a trusted proxy that sets X-Forwarded-For;
    # without this every user would share the proxy's IP in the rate limiter.
    RATE_LIMIT_TRUST_FORWARDED_FOR = "true"
  })

  job_env = merge(local.common_env, {
    AZURE_CLIENT_ID = module.identity.identities["job"].client_id
  })
}
