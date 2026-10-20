variable "name" {
  description = "Registry name (globally unique, letters and digits only)."
  type        = string
}

variable "resource_group_name" {
  description = "Resource group to create the registry in."
  type        = string
}

variable "location" {
  description = "Azure region."
  type        = string
}

variable "tags" {
  description = "Tags for the registry."
  type        = map(string)
}
