# Log Analytics workspace: where the Container Apps environment sends the
# containers' stdout/stderr (our logs, guardrail events, ingest reports) and
# system events. Query it with KQL in the portal (Logs).

resource "azurerm_log_analytics_workspace" "this" {
  name                = var.name
  resource_group_name = var.resource_group_name
  location            = var.location
  sku                 = "PerGB2018" # pay per GB ingested
  retention_in_days   = var.retention_days
  daily_quota_gb      = var.daily_quota_gb
  tags                = var.tags
}
