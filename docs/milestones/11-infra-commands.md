# 11 — Infra commands (cheat sheet)

Every command to deploy the Runbook Assistant to Azure, keep it updated, and take it down. Run them in PowerShell from the `infra` folder unless a step says otherwise:

```powershell
cd D:\Projects\DevOps-Runbook-Assistant\Project-Runbook-Assistant\runbook-assistant\infra
```

Details and explanations: [infra/README.md](../../infra/README.md).

---

## 0. Before you start

```powershell
az login
az account set --subscription c8bd8aa5-3aba-4806-add2-357985a59ccc
```

Tools: Azure CLI, Terraform 1.16+, git, plus tflint and trivy for the checks.
Config files: `environments/dev/dev.tfvars` and `environments/dev/backend.hcl` (no secrets in either).

---

## 1. First deployment (from nothing)

```powershell
# State storage for Terraform (once per subscription; safe to re-run)
./scripts/bootstrap-state.ps1 -Location uksouth
./scripts/init.ps1

# Stage 1: base infrastructure (registry, Key Vault, Search, logs, identities, budget)
./scripts/plan.ps1
./scripts/apply.ps1                  # type: apply

# API keys into Key Vault (use a separate PowerShell window, not the VS Code terminal)
./scripts/set-secrets.ps1

# Build the image in Azure, tagged with the current commit
cd ..; git add .; git commit -m "..."; cd infra
./scripts/build-image.ps1

# Stage 2: the app and the ingest job
./scripts/plan.ps1 -DeployApps
./scripts/apply.ps1                  # type: apply

# Load the runbooks into Azure AI Search
./scripts/run-ingest.ps1

# Open the app
terraform output app_url
```

---

## 2. Everyday changes

| What changed | Commands |
|---|---|
| App code | commit, then `./scripts/build-image.ps1`, `./scripts/plan.ps1 -DeployApps`, `./scripts/apply.ps1` |
| Runbooks (`data/runbooks`) | same as app code, then `./scripts/run-ingest.ps1` |
| Terraform files only | commit, then `./scripts/plan.ps1 -DeployApps -ImageTag <current image tag>`, `./scripts/apply.ps1` |
| An API key | `./scripts/set-secrets.ps1` (applies it to the app and job immediately) |
| Roll back | `./scripts/plan.ps1 -DeployApps -ImageTag <older tag>`, `./scripts/apply.ps1` |

The current image tag: `terraform output image`.

---

## 3. Checking things

```powershell
terraform output                                                  # names, URL, image
az containerapp logs show -n ca-rbk-dev-app -g rg-rbk-dev --tail 100   # app logs

# Ingest job logs (they arrive a few minutes late)
$ws = az monitor log-analytics workspace list -g rg-rbk-dev --query "[0].customerId" -o tsv
az monitor log-analytics query -w $ws --analytics-query "ContainerAppConsoleLogs_CL | where ContainerJobName_s == 'caj-rbk-dev-ingest' and TimeGenerated > ago(30m) | order by TimeGenerated asc | project TimeGenerated, Log_s" -o table

# Restart the app
$rev = az containerapp show -n ca-rbk-dev-app -g rg-rbk-dev --query properties.latestRevisionName -o tsv
az containerapp revision restart -n ca-rbk-dev-app -g rg-rbk-dev --revision $rev
```

Local checks (no Azure changes):
```powershell
terraform fmt -recursive -check
terraform validate
tflint --recursive
trivy config --ignorefile .trivyignore.yaml --tf-vars environments/dev/dev.tfvars .
```

---

## 4. Taking it down

**Pause:** remove only the app and job. The URL stops working; registry, Key Vault and Search stay (about $0.17/day).
```powershell
./scripts/destroy.ps1 -AppsOnly      # type: apply
```
Bring it back (same URL, no rebuild or re-ingest):
```powershell
./scripts/plan.ps1 -DeployApps -ImageTag <image tag>
./scripts/apply.ps1
```

**Remove everything** in `rg-rbk-dev` (about $0 afterwards; the Terraform state is kept):
```powershell
./scripts/destroy.ps1                # type: rg-rbk-dev
az group list --query "[].name" -o tsv   # check: rg-rbk-dev is gone
```
Bring it back: repeat section 1 from "Stage 1". The new resources get a new random suffix, so a **new URL**.

**Finished with the project for good:** also delete the Terraform state storage. Do this only after "remove everything".
```powershell
az group delete -n rg-runbook-tfstate
```

---

## 5. If something goes wrong

| Symptom | Fix |
|---|---|
| `Error code: 400` from OpenAI, no message | The key in Key Vault is broken. Run `./scripts/set-secrets.ps1` again |
| 403 from Search right after deploying | Roles are still propagating. Wait 5–10 minutes and retry |
| App still uses an old key | `./scripts/set-secrets.ps1` refreshes and restarts it (Container Apps caches Key Vault values for about 30 minutes) |
| Plan shows unexpected changes or deletions | Don't apply; check what changed with `terraform show -no-color tfplan` |
| `Error acquiring the state lock` | Another run is active, or one crashed. Wait, or `terraform force-unlock <lock id>` |
