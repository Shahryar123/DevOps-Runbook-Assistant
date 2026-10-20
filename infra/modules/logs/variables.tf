variable "name" {
  description = "Workspace name."
  type        = string
}

variable "resource_group_name" {
  description = "Resource group to create the workspace in."
  type        = string
}

variable "location" {
  description = "Azure region."
  type        = string
}

variable "retention_days" {
  description = "Days to keep logs."
  type        = number
}

variable "daily_quota_gb" {
  description = "Daily ingestion cap in GB (-1 = unlimited)."
  type        = number
}

variable "tags" {
  description = "Tags for the workspace."
  type        = map(string)
}
