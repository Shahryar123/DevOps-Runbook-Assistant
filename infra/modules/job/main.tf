# The ingest job: same image as the app, command overridden to run ingest.
# A Container Apps Job runs to completion and stops (no ingress, no replicas
# left running), billed only while it runs. Started by run-ingest.ps1.
#
# One writer only: Azure AI Search has no lock file, so parallelism = 1 and
# replica_completion_count = 1 guarantee a single ingest replica per
# execution. Don't start a second execution while one is running.

resource "azurerm_container_app_job" "this" {
  name                         = var.name
  resource_group_name          = var.resource_group_name
  location                     = var.location
  container_app_environment_id = var.container_app_environment_id
  workload_profile_name        = "Consumption" # see modules/containerapp
  replica_timeout_in_seconds   = var.timeout_seconds
  replica_retry_limit          = var.retry_limit # ingest is idempotent, so a retry is safe
  tags                         = var.tags

  manual_trigger_config {
    parallelism              = 1
    replica_completion_count = 1
  }

  identity {
    type         = "UserAssigned"
    identity_ids = [var.identity_id]
  }

  registry {
    server   = var.registry_server
    identity = var.identity_id
  }

  dynamic "secret" {
    for_each = var.secrets
    content {
      name                = secret.key
      key_vault_secret_id = secret.value.key_vault_secret_id
      identity            = var.identity_id
    }
  }

  template {
    container {
      name    = "ingest"
      image   = var.image
      command = var.command
      cpu     = var.cpu
      memory  = var.memory

      dynamic "env" {
        for_each = var.env
        content {
          name  = env.key
          value = env.value
        }
      }

      dynamic "env" {
        for_each = var.secrets
        content {
          name        = env.value.env_name
          secret_name = env.key
        }
      }
    }
  }
}
