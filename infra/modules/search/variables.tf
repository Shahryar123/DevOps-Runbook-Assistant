variable "name" {
  description = "Search service name (globally unique; lowercase letters, digits, dashes)."
  type        = string
}

variable "resource_group_name" {
  description = "Resource group to create the service in."
  type        = string
}

variable "location" {
  description = "Azure region."
  type        = string
}

variable "sku" {
  description = "Pricing tier (free, basic, standard, ...)."
  type        = string
}

variable "local_auth_enabled" {
  description = "Also accept API keys (fallback). false = Entra ID / RBAC only."
  type        = bool
}

variable "tags" {
  description = "Tags for the service."
  type        = map(string)
}
