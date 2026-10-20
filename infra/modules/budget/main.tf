# Monthly cost budget on the resource group, with email alerts. A budget
# only ALERTS; it never stops resources. Cost data lags by several hours.

# Azure requires the start date to be the first day of a month. Record the
# creation time once (time_static keeps it in state) and use that month.
resource "time_static" "created" {}

resource "azurerm_consumption_budget_resource_group" "this" {
  name              = var.name
  resource_group_id = var.resource_group_id
  amount            = var.amount
  time_grain        = "Monthly"

  time_period {
    start_date = formatdate("YYYY-MM-01'T'00:00:00'Z'", time_static.created.rfc3339)
  }

  dynamic "notification" {
    for_each = toset(var.thresholds)
    content {
      enabled        = true
      threshold      = notification.value
      operator       = "GreaterThanOrEqualTo"
      threshold_type = "Actual"
      contact_emails = var.contact_emails
    }
  }

  lifecycle {
    # Changing the start date would recreate the budget; it never needs to.
    ignore_changes = [time_period]
  }
}
