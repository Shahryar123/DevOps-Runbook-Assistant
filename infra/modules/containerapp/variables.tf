variable "name" {
  description = "Container App name."
  type        = string
}

variable "resource_group_name" {
  description = "Resource group to create the app in."
  type        = string
}

variable "container_app_environment_id" {
  description = "Container Apps environment to run in."
  type        = string
}

variable "identity_id" {
  description = "Resource ID of the user-assigned identity (registry pull, Key Vault references, Azure SDK)."
  type        = string
}

variable "registry_server" {
  description = "ACR login server to pull the image from."
  type        = string
}

variable "image" {
  description = "Full image reference, e.g. acr.azurecr.io/runbook-assistant:<sha>."
  type        = string
}

variable "cpu" {
  description = "vCPU per replica."
  type        = number
}

variable "memory" {
  description = "Memory per replica, e.g. 1Gi."
  type        = string
}

variable "min_replicas" {
  description = "Minimum replicas (0 = scale to zero)."
  type        = number
}

variable "max_replicas" {
  description = "Maximum replicas."
  type        = number
}

variable "target_port" {
  description = "Port Streamlit listens on."
  type        = number
  default     = 8501
}

variable "health_path" {
  description = "Streamlit health endpoint used by all probes."
  type        = string
  default     = "/_stcore/health"
}

variable "env" {
  description = "Plain environment variables."
  type        = map(string)
}

variable "secrets" {
  description = "Key Vault secret name -> { env_name, key_vault_secret_id }."
  type = map(object({
    env_name            = string
    key_vault_secret_id = string
  }))
}

variable "tags" {
  description = "Tags for the app."
  type        = map(string)
}
