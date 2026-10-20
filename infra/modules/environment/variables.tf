variable "name" {
  description = "Environment name."
  type        = string
}

variable "resource_group_name" {
  description = "Resource group to create the environment in."
  type        = string
}

variable "location" {
  description = "Azure region."
  type        = string
}

variable "log_analytics_workspace_id" {
  description = "Log Analytics workspace that receives the container logs."
  type        = string
}

variable "tags" {
  description = "Tags for the environment."
  type        = map(string)
}
