# Terraform and provider versions.
#
# A provider is the plugin that translates Terraform resources into API calls
# (azurerm -> Azure Resource Manager). Pinning both the Terraform CLI and the
# providers means everyone (and every CI run) plans with the same code;
# .terraform.lock.hcl then pins the exact provider build and its checksums.

terraform {
  required_version = "~> 1.16" # >= 1.16, < 2.0

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 5.10" # >= 5.10, < 6.0 (v5 is the current major)
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.9"
    }
    time = {
      source  = "hashicorp/time"
      version = "~> 0.14"
    }
  }

  # Remote state in Azure Blob Storage. The settings (resource group, account,
  # container, key) live in environments/<env>/backend.hcl and are passed with
  # `terraform init -backend-config=...`; backend blocks can't use variables.
  # use_azuread_auth: authenticate with Entra ID (your az login), never with
  # storage account keys (shared key access is disabled on the account).
  backend "azurerm" {
    use_azuread_auth = true
  }
}
