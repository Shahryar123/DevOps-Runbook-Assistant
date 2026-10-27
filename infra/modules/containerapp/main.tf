# The Streamlit app as a Container App.
#
# Revisions: every change to `template` (image tag, env, CPU, ...) creates a
# new immutable revision. revision_mode = "Single" sends all traffic to the
# newest healthy revision and deactivates the old one, so a deploy is just
# "apply with a new image_tag". (Multiple mode would allow traffic splitting.)
#
# Scaling: min 0 / max 1. At 0 replicas nothing is billed; the next request
# starts a replica first (cold start, ~10-30 s). Max 1 because Streamlit keeps
# each chat (and our rate limiters) in that replica's memory.

resource "azurerm_container_app" "this" {
  name                         = var.name
  resource_group_name          = var.resource_group_name
  container_app_environment_id = var.container_app_environment_id
  # The environment's only profile (see modules/environment). Azure sets it
  # anyway; declaring it stops Terraform planning to remove it.
  workload_profile_name  = "Consumption"
  revision_mode          = "Single"
  max_inactive_revisions = 3
  tags                   = var.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [var.identity_id]
  }

  # Pull from ACR with the managed identity (AcrPull), no registry password.
  registry {
    server   = var.registry_server
    identity = var.identity_id
  }

  # Key Vault references: the platform fetches each secret from Key Vault
  # with the identity (Key Vault Secrets User). Only the reference (a URL) is
  # in Terraform and state, never the value.
  dynamic "secret" {
    for_each = var.secrets
    content {
      name                = secret.key
      key_vault_secret_id = secret.value.key_vault_secret_id
      identity            = var.identity_id
    }
  }

  ingress {
    external_enabled           = true # public HTTPS endpoint
    target_port                = var.target_port
    transport                  = "auto" # HTTP/1.1 + HTTP/2; WebSocket upgrades work (Streamlit needs them)
    allow_insecure_connections = false  # HTTP is redirected to HTTPS

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas = var.min_replicas
    max_replicas = var.max_replicas

    container {
      name   = "app"
      image  = var.image
      cpu    = var.cpu
      memory = var.memory

      dynamic "env" {
        for_each = var.env
        content {
          name  = env.key
          value = env.value
        }
      }

      # Secrets become environment variables (OPENAI_API_KEY, ...).
      dynamic "env" {
        for_each = var.secrets
        content {
          name        = env.value.env_name
          secret_name = env.key
        }
      }

      # Startup: give Streamlit up to ~2 minutes to come up (cold start)
      # before liveness checks begin.
      startup_probe {
        transport               = "HTTP"
        port                    = var.target_port
        path                    = var.health_path
        interval_seconds        = 5
        timeout                 = 3
        failure_count_threshold = 24
      }

      # Liveness: restart the container if Streamlit stops answering.
      liveness_probe {
        transport               = "HTTP"
        port                    = var.target_port
        path                    = var.health_path
        interval_seconds        = 30
        timeout                 = 5
        failure_count_threshold = 3
      }

      # Readiness: send traffic only while Streamlit answers.
      readiness_probe {
        transport               = "HTTP"
        port                    = var.target_port
        path                    = var.health_path
        interval_seconds        = 10
        timeout                 = 5
        failure_count_threshold = 3
        success_count_threshold = 1
      }
    }
  }
}
