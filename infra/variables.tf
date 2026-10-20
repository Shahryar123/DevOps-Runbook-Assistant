# Input variables: the knobs of this configuration. Values come from
# environments/<env>/<env>.tfvars (and -var on the command line for
# deploy_apps / image_tag). No secrets here: secret VALUES go straight into
# Key Vault with scripts/set-secrets.ps1 and never pass through Terraform.

# --- Subscription, naming, tags ---------------------------------------------

variable "subscription_id" {
  description = "Azure subscription to deploy into (checked against `az account show` by the scripts)."
  type        = string

  validation {
    condition     = can(regex("^[0-9a-fA-F-]{36}$", var.subscription_id))
    error_message = "subscription_id must be a subscription GUID."
  }
}

variable "location" {
  description = "Azure region for all resources."
  type        = string
}

variable "prefix" {
  description = "Short project prefix used in every resource name (lowercase letters/digits, 2-6 chars)."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9]{1,5}$", var.prefix))
    error_message = "prefix must be 2-6 lowercase letters/digits, starting with a letter."
  }
}

variable "environment" {
  description = "Environment name (dev, test, prod); part of names and tags."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9]{1,4}$", var.environment))
    error_message = "environment must be 2-5 lowercase letters/digits."
  }
}

variable "owner" {
  description = "Value of the `owner` tag (a name or team, not a secret)."
  type        = string
}

variable "owner_object_id" {
  description = "Entra object ID of the human operator (gets Key Vault Secrets Officer and Search data roles). null = whoever runs Terraform."
  type        = string
  default     = null
}

variable "owner_principal_type" {
  description = "Principal type of owner_object_id: User (a person) or ServicePrincipal (e.g. a CI identity)."
  type        = string
  default     = "User"

  validation {
    condition     = contains(["User", "Group", "ServicePrincipal"], var.owner_principal_type)
    error_message = "owner_principal_type must be User, Group or ServicePrincipal."
  }
}

variable "extra_tags" {
  description = "Additional tags merged into the standard ones."
  type        = map(string)
  default     = {}
}

# --- Deployment stage ---------------------------------------------------------

variable "deploy_apps" {
  description = "false = base infrastructure only; true = also the Container App and the ingest Job (needs secrets in Key Vault and the image in ACR first)."
  type        = bool
  default     = false
}

variable "image_repository" {
  description = "Repository name of the image in ACR."
  type        = string
  default     = "runbook-assistant"
}

variable "image_tag" {
  description = "Image tag to deploy (the git commit SHA from build-image.ps1). Required when deploy_apps = true."
  type        = string
  default     = null

  validation {
    condition     = !var.deploy_apps || (var.image_tag != null && var.image_tag != "")
    error_message = "image_tag is required when deploy_apps = true."
  }
  validation {
    condition     = var.image_tag == null || var.image_tag != "latest"
    error_message = "Use an immutable tag (the git SHA), not 'latest': otherwise Terraform can't tell a new image from the old one."
  }
}

variable "role_propagation_wait" {
  description = "How long to wait after creating role assignments before creating the app/job (RBAC propagation)."
  type        = string
  default     = "120s"
}

# --- Logs ---------------------------------------------------------------------

variable "log_retention_days" {
  description = "Log Analytics retention (30 is the minimum and free)."
  type        = number
  default     = 30
}

variable "log_daily_quota_gb" {
  description = "Daily ingestion cap for Log Analytics in GB (-1 = unlimited). A cost guard."
  type        = number
  default     = 1
}

# --- Key Vault ----------------------------------------------------------------

variable "kv_purge_protection_enabled" {
  description = "Purge protection: once on, it can't be turned off and deleted vaults can't be purged until retention ends. Off for dev so it can be cleaned up."
  type        = bool
  default     = false
}

variable "kv_soft_delete_retention_days" {
  description = "Days a deleted vault/secret stays recoverable (7-90)."
  type        = number
  default     = 7
}

# --- Azure AI Search ----------------------------------------------------------

variable "search_sku" {
  description = "Search tier. free: 50 MB, 3 indexes, one per subscription."
  type        = string
  default     = "free"
}

variable "search_index_name" {
  description = "Index the ingest job creates and the app queries (not created by Terraform)."
  type        = string
  default     = "runbooks"
}

variable "search_local_auth_enabled" {
  description = "Fallback only: also accept API keys on the Search service and pass the admin key (Key Vault secret search-admin-key) to the containers. Default false = Entra ID (RBAC) only."
  type        = bool
  default     = false
}

# --- Container App (Streamlit) and Job (ingest) -------------------------------

variable "app_cpu" {
  description = "vCPU per app replica (0.25 steps; memory must be 2x CPU in Gi)."
  type        = number
  default     = 0.5
}

variable "app_memory" {
  description = "Memory per app replica, e.g. 1Gi (2x app_cpu)."
  type        = string
  default     = "1Gi"
}

variable "app_min_replicas" {
  description = "0 = scale to zero when idle (cheapest; cold start on the next request)."
  type        = number
  default     = 0
}

variable "app_max_replicas" {
  description = "Keep 1: Streamlit chat state and the rate limiters live in one replica's memory."
  type        = number
  default     = 1
}

variable "job_cpu" {
  description = "vCPU for the ingest job."
  type        = number
  default     = 0.5
}

variable "job_memory" {
  description = "Memory for the ingest job (2x job_cpu)."
  type        = string
  default     = "1Gi"
}

variable "job_timeout_seconds" {
  description = "Max run time of one ingest execution before it's killed."
  type        = number
  default     = 1800
}

variable "job_retry_limit" {
  description = "Retries of a failed ingest (safe: ingest is idempotent)."
  type        = number
  default     = 1
}

variable "retrieval_mode" {
  description = "RETRIEVAL_MODE for the containers: hybrid (keyword + vector) or vector."
  type        = string
  default     = "hybrid"

  validation {
    condition     = contains(["hybrid", "vector"], var.retrieval_mode)
    error_message = "retrieval_mode must be hybrid or vector."
  }
}

variable "langsmith_project" {
  description = "LangSmith project the deployed app traces to."
  type        = string
  default     = "runbook-assistant-prod"
}

# --- Budget -------------------------------------------------------------------

variable "budget_amount" {
  description = "Monthly budget for the resource group, in the billing currency."
  type        = number
  default     = 20
}

variable "budget_alert_emails" {
  description = "Who gets the 80% / 100% budget alerts."
  type        = list(string)

  validation {
    condition     = length(var.budget_alert_emails) > 0
    error_message = "Give at least one budget alert email."
  }
}
