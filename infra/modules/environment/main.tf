# Container Apps environment: the shared, serverless "cluster" the app and
# the job run in (networking, ingress, scaling, logs). Consumption plan only:
# billed per second of vCPU/memory actually used, nothing while scaled to 0.

resource "azurerm_container_app_environment" "this" {
  name                = var.name
  resource_group_name = var.resource_group_name
  location            = var.location

  # azurerm v5: logs_destination must be set explicitly to send container
  # logs to Log Analytics (otherwise they are only streamed live).
  logs_destination           = "log-analytics"
  log_analytics_workspace_id = var.log_analytics_workspace_id

  tags = var.tags
}
