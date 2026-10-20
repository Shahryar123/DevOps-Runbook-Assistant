variable "name" {
  description = "Budget name."
  type        = string
}

variable "resource_group_id" {
  description = "Resource group whose costs are tracked."
  type        = string
}

variable "amount" {
  description = "Monthly amount in the billing currency."
  type        = number
}

variable "contact_emails" {
  description = "Who receives the alerts."
  type        = list(string)
}

variable "thresholds" {
  description = "Alert at these percentages of the amount (actual cost)."
  type        = list(number)
  default     = [80, 100]
}
