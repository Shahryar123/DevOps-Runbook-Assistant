provider "azurerm" {
  # Required for plan/apply since azurerm v4: no silent fallback to whatever
  # subscription `az account` happens to point at.
  subscription_id = var.subscription_id

  # v5 registers no Resource Providers by default. Register exactly the ones
  # this configuration uses (a one-time, idempotent operation per subscription).
  resource_providers_to_register = [
    "Microsoft.App",                 # Container Apps (environment, app, job)
    "Microsoft.Consumption",         # budget
    "Microsoft.ContainerRegistry",   # ACR
    "Microsoft.KeyVault",            # Key Vault
    "Microsoft.ManagedIdentity",     # user-assigned identities
    "Microsoft.OperationalInsights", # Log Analytics
    "Microsoft.Search",              # Azure AI Search
  ]

  features {
    key_vault {
      # With purge protection off (dev), purge on destroy so the vault name
      # can be reused right away. With it on, Azure forbids purging anyway.
      purge_soft_delete_on_destroy    = !var.kv_purge_protection_enabled
      recover_soft_deleted_key_vaults = true
    }
    log_analytics_workspace {
      # Dev: really delete the workspace on destroy (no 14-day soft delete).
      permanently_delete_on_destroy = true
    }
    resource_group {
      # Refuse to delete the resource group if something not managed by
      # Terraform was created in it (safety net).
      prevent_deletion_if_contains_resources = true
    }
  }
}
