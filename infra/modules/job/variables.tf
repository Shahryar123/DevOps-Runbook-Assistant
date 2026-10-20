variable "name" {
  description = "Job name."
  type        = string
}

variable "resource_group_name" {
  description = "Resource group to create the job in."
  type        = string
}

variable "location" {
  description = "Azure region."
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
  description = "Full image reference (same image as the app)."
  type        = string
}

variable "command" {
  description = "Command that replaces the image's default (the Streamlit app)."
  type        = list(string)
}

variable "cpu" {
  description = "vCPU for the job."
  type        = number
}

variable "memory" {
  description = "Memory for the job, e.g. 1Gi."
  type        = string
}

variable "timeout_seconds" {
  description = "Max run time of one execution."
  type        = number
}

variable "retry_limit" {
  description = "Retries of a failed execution."
  type        = number
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
  description = "Tags for the job."
  type        = map(string)
}
