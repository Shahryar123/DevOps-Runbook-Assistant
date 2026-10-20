# Backend settings for the dev state (not secret: names only, no keys).
# Used by: terraform init -backend-config=environments/dev/backend.hcl
# and read by scripts/bootstrap-state.ps1, which creates these resources.
#
# storage_account_name must be globally unique (3-24 lowercase letters and
# digits).

resource_group_name  = "rg-runbook-tfstate"
storage_account_name = "iqtechrunbookassistant"
container_name       = "tfstate"
key                  = "dev.tfstate"
use_azuread_auth     = true
