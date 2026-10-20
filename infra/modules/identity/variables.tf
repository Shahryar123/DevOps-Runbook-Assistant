variable "names" {
  description = "Identities to create: key (e.g. app, job) -> resource name."
  type        = map(string)
}

variable "resource_group_name" {
  description = "Resource group to create the identities in."
  type        = string
}

variable "location" {
  description = "Azure region."
  type        = string
}

variable "tags" {
  description = "Tags for the identities."
  type        = map(string)
}
