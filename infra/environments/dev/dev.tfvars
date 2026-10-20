# Dev environment values. NO SECRETS in this file: it's committed.
# API keys go into Key Vault with scripts/set-secrets.ps1.
# deploy_apps and image_tag are passed by scripts/plan.ps1, not set here.

# Subscription "Azure subscription 1" (az account show --query id -o tsv).
subscription_id = "c8bd8aa5-3aba-4806-add2-357985a59ccc"
location        = "uksouth"

prefix      = "rbk"
environment = "dev"
owner       = "IQTECH" # tag value

# null = the signed-in user running Terraform gets Key Vault Secrets Officer.
# owner_object_id = "00000000-0000-0000-0000-000000000000"

# Sizes (small defaults; memory = 2 x CPU in Gi)
app_cpu          = 0.5
app_memory       = "1Gi"
app_min_replicas = 0
app_max_replicas = 1
job_cpu          = 0.5
job_memory       = "1Gi"

search_sku        = "free"
search_index_name = "runbooks"
retrieval_mode    = "hybrid"
langsmith_project = "runbook-assistant-prod"

kv_purge_protection_enabled = false

budget_amount       = 20
budget_alert_emails = ["muhammadshahryar135@gmail.com"]
