variable "name" {
  description = "Foundry resource name; also its custom subdomain (globally unique; lowercase letters, digits, dashes)."
  type        = string
}

variable "project_name" {
  description = "Foundry project name."
  type        = string
}

variable "resource_group_name" {
  description = "Resource group to create the Foundry resource in."
  type        = string
}

variable "location" {
  description = "Azure region."
  type        = string
}

variable "deployments" {
  description = "Model deployments, keyed by deployment name (= the `model` the app sends)."
  type = map(object({
    model    = string
    version  = optional(string) # null = the model's current default version
    format   = optional(string, "OpenAI")
    sku      = optional(string, "GlobalStandard")
    capacity = number
    # true = use the content filter below (chat models); false = the default.
    content_filter = optional(bool, false)
  }))
}

variable "content_filter" {
  description = "Custom content filter (RAI policy) for chat deployments. Renaming it replaces the policy."
  type = object({
    name               = string
    severity_threshold = string # Low (strictest), Medium or High
  })

  validation {
    condition     = contains(["Low", "Medium", "High"], var.content_filter.severity_threshold)
    error_message = "severity_threshold must be Low, Medium or High."
  }
}

variable "tags" {
  description = "Tags for the Foundry resource and project."
  type        = map(string)
}
