variable "name" {
  description = "Vault name (globally unique, 3-24 chars)."
  type        = string
}

variable "resource_group_name" {
  description = "Resource group to create the vault in."
  type        = string
}

variable "location" {
  description = "Azure region."
  type        = string
}

variable "tenant_id" {
  description = "Entra tenant that authenticates requests to the vault."
  type        = string
}

variable "purge_protection_enabled" {
  description = "Prevent purging deleted vaults/secrets until retention ends (irreversible once on)."
  type        = bool
}

variable "soft_delete_retention_days" {
  description = "Days deleted vaults/secrets stay recoverable (7-90)."
  type        = number
}

variable "tags" {
  description = "Tags for the vault."
  type        = map(string)
}
