<#
.SYNOPSIS
  Tear down the dev environment (never the state backend).

.DESCRIPTION
  Default: terraform destroy of everything this configuration manages (the
  rg-<prefix>-dev resource group and its contents), after you type the
  resource group name. The state backend (rg-runbook-tfstate) is not part of
  this configuration, so it can't be touched; delete it by hand only when
  you're done with the project for good.

  -AppsOnly: remove just the Container App and Job (plan with
  deploy_apps = false, then the usual apply.ps1 review and confirmation).

.EXAMPLE
  ./scripts/destroy.ps1 -AppsOnly
  ./scripts/destroy.ps1
#>
param([switch] $AppsOnly)

. "$PSScriptRoot/_common.ps1"
Assert-Tool 'terraform'
Assert-AzLogin | Out-Null

if ($AppsOnly) {
    & "$PSScriptRoot/plan.ps1"
    & "$PSScriptRoot/apply.ps1"
    return
}

$rg = Get-TfOutput 'resource_group_name'
Write-Host "This permanently deletes resource group '$rg' and everything in it:" -ForegroundColor Yellow
Write-Host '  app, ingest job, registry + images, Key Vault + secrets (purged), Search + index, logs, identities, budget.' -ForegroundColor Yellow
Confirm-Typed -Expected $rg -Prompt 'Destroy'

Push-Location $InfraRoot
try {
    # -auto-approve: the typed confirmation above replaces Terraform's prompt.
    Invoke-Native { terraform destroy -input=false -auto-approve "-var-file=$VarFile" -var 'deploy_apps=false' }
}
finally { Pop-Location }

Write-Host ''
Write-Host "Destroyed '$rg'. The state backend is untouched." -ForegroundColor Green
