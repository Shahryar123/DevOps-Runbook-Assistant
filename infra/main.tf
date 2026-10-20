# Root module: one resource group for the environment, the building blocks
# (child modules in ./modules), the role assignments that connect them, and
# the Container App + Job when deploy_apps = true.
#
# Stages (see README.md):
#   1. deploy_apps = false  -> base infrastructure
#   2. set-secrets.ps1, build-image.ps1
#   3. deploy_apps = true, image_tag = <git sha>  -> app + ingest job

resource "azurerm_resource_group" "this" {
  name     = local.names.resource_group
  location = var.location
  tags     = local.tags
}

module "logs" {
  source = "./modules/logs"

  name                = local.names.log_analytics
  resource_group_name = azurerm_resource_group.this.name
  location            = var.location
  retention_days      = var.log_retention_days
  daily_quota_gb      = var.log_daily_quota_gb
  tags                = local.tags
}

module "acr" {
  source = "./modules/acr"

  name                = local.names.acr
  resource_group_name = azurerm_resource_group.this.name
  location            = var.location
  tags                = local.tags
}

module "keyvault" {
  source = "./modules/keyvault"

  name                       = local.names.key_vault
  resource_group_name        = azurerm_resource_group.this.name
  location                   = var.location
  tenant_id                  = data.azurerm_client_config.current.tenant_id
  purge_protection_enabled   = var.kv_purge_protection_enabled
  soft_delete_retention_days = var.kv_soft_delete_retention_days
  tags                       = local.tags
}

module "search" {
  source = "./modules/search"

  name                = local.names.search
  resource_group_name = azurerm_resource_group.this.name
  location            = var.location
  sku                 = var.search_sku
  local_auth_enabled  = var.search_local_auth_enabled
  tags                = local.tags
}

module "identity" {
  source = "./modules/identity"

  resource_group_name = azurerm_resource_group.this.name
  location            = var.location
  names = {
    app = local.names.id_app
    job = local.names.id_job
  }
  tags = local.tags
}

module "environment" {
  source = "./modules/environment"

  name                       = local.names.environment
  resource_group_name        = azurerm_resource_group.this.name
  location                   = var.location
  log_analytics_workspace_id = module.logs.id
  tags                       = local.tags
}

module "budget" {
  source = "./modules/budget"

  name              = local.names.budget
  resource_group_id = azurerm_resource_group.this.id
  amount            = var.budget_amount
  contact_emails    = var.budget_alert_emails
}

# --- Role assignments (RBAC) --------------------------------------------------
# Each entry: who (principal) gets which role on which single resource (scope).
# Scoped to the resource, not the resource group: least privilege.

locals {
  app_principal = module.identity.identities["app"].principal_id
  job_principal = module.identity.identities["job"].principal_id

  role_assignments = {
    # id-app: pull the image, read its secrets, query the index.
    app_acr_pull     = { scope = module.acr.id, role = "AcrPull", principal = local.app_principal, type = "ServicePrincipal" }
    app_kv_secrets   = { scope = module.keyvault.id, role = "Key Vault Secrets User", principal = local.app_principal, type = "ServicePrincipal" }
    app_search_query = { scope = module.search.id, role = "Search Index Data Reader", principal = local.app_principal, type = "ServicePrincipal" }
    # LangChain's AzureSearch reads the index definition on start-up
    # (GET /indexes/<name>), which the data reader role doesn't allow.
    # Reader = read-only control plane + object definitions; no keys, no writes.
    app_search_definition = { scope = module.search.id, role = "Reader", principal = local.app_principal, type = "ServicePrincipal" }

    # id-job: pull the image, read its secrets, write documents, and
    # create/delete/reset the index (Search Service Contributor).
    job_acr_pull       = { scope = module.acr.id, role = "AcrPull", principal = local.job_principal, type = "ServicePrincipal" }
    job_kv_secrets     = { scope = module.keyvault.id, role = "Key Vault Secrets User", principal = local.job_principal, type = "ServicePrincipal" }
    job_search_data    = { scope = module.search.id, role = "Search Index Data Contributor", principal = local.job_principal, type = "ServicePrincipal" }
    job_search_service = { scope = module.search.id, role = "Search Service Contributor", principal = local.job_principal, type = "ServicePrincipal" }

    # You: set secrets; inspect the index and run ingest locally with az login.
    owner_kv_secrets     = { scope = module.keyvault.id, role = "Key Vault Secrets Officer", principal = local.owner_object_id, type = var.owner_principal_type }
    owner_search_data    = { scope = module.search.id, role = "Search Index Data Contributor", principal = local.owner_object_id, type = var.owner_principal_type }
    owner_search_service = { scope = module.search.id, role = "Search Service Contributor", principal = local.owner_object_id, type = var.owner_principal_type }
  }
}

resource "azurerm_role_assignment" "this" {
  for_each = local.role_assignments

  scope                = each.value.scope
  role_definition_name = each.value.role
  principal_id         = each.value.principal
  # Setting the type avoids failures when a brand-new identity hasn't
  # replicated through Entra ID yet.
  principal_type = each.value.type
}

# Role assignments take a while to take effect. Without this pause the first
# Container App revision can fail to pull the image or read a Key Vault secret.
# time_sleep only waits when it's created (first apply), not on later plans.
resource "time_sleep" "rbac_propagation" {
  create_duration = var.role_propagation_wait

  depends_on = [azurerm_role_assignment.this]
}

# --- Apps (stage 2) -----------------------------------------------------------
# count = 0 or 1: the module exists only when deploy_apps = true.

module "app" {
  source = "./modules/containerapp"
  count  = var.deploy_apps ? 1 : 0

  name                         = local.names.app
  resource_group_name          = azurerm_resource_group.this.name
  container_app_environment_id = module.environment.id
  identity_id                  = module.identity.identities["app"].id
  registry_server              = module.acr.login_server
  image                        = local.image
  cpu                          = var.app_cpu
  memory                       = var.app_memory
  min_replicas                 = var.app_min_replicas
  max_replicas                 = var.app_max_replicas
  env                          = local.app_env
  secrets                      = local.container_secrets
  tags                         = local.tags

  depends_on = [time_sleep.rbac_propagation]
}

module "ingest_job" {
  source = "./modules/job"
  count  = var.deploy_apps ? 1 : 0

  name                         = local.names.job
  resource_group_name          = azurerm_resource_group.this.name
  location                     = var.location
  container_app_environment_id = module.environment.id
  identity_id                  = module.identity.identities["job"].id
  registry_server              = module.acr.login_server
  image                        = local.image
  command                      = ["python", "-m", "runbook_assistant.ingest"]
  cpu                          = var.job_cpu
  memory                       = var.job_memory
  timeout_seconds              = var.job_timeout_seconds
  retry_limit                  = var.job_retry_limit
  env                          = local.job_env
  secrets                      = local.container_secrets
  tags                         = local.tags

  depends_on = [time_sleep.rbac_propagation]
}
