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

  # Azure now creates every environment as a "workload profiles" environment
  # with a built-in serverless profile named Consumption (what the app and
  # job run on). Declaring it keeps code and Azure in sync; without it,
  # Terraform plans to remove the profile, and removing all profiles forces
  # the environment to be recreated. Counts don't apply to Consumption.
  workload_profile {
    name                  = "Consumption"
    workload_profile_type = "Consumption"
  }

  tags = var.tags
}
