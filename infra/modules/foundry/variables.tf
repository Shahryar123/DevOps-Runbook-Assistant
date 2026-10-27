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
  }))
}

variable "tags" {
  description = "Tags for the Foundry resource and project."
  type        = map(string)
}
