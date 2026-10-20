<#
.SYNOPSIS
  terraform plan for dev, saved to infra/tfplan (apply it with apply.ps1).

.DESCRIPTION
  Without -DeployApps: base infrastructure only (deploy_apps = false). If the
  app and job already exist, this plan REMOVES them; read the plan.
  With -DeployApps: also the Container App and Job, running the image tagged
  -ImageTag (default: the current git commit SHA, as built by build-image.ps1).

.EXAMPLE
  ./scripts/plan.ps1                                   # stage 1
  ./scripts/plan.ps1 -DeployApps                       # stage 2, tag = git SHA
  ./scripts/plan.ps1 -DeployApps -ImageTag 1a2b3c4d5e6f
#>
param(
    [switch] $DeployApps,
    [string] $ImageTag
)

. "$PSScriptRoot/_common.ps1"
Assert-Tool 'terraform'
Assert-AzLogin | Out-Null

$planArgs = @('plan', '-input=false', "-var-file=$VarFile", "-out=$PlanFile")
if ($DeployApps) {
    if (-not $ImageTag) { $ImageTag = Get-GitSha }
    Write-Host "deploy_apps = true, image_tag = $ImageTag" -ForegroundColor Cyan
    $planArgs += @('-var', 'deploy_apps=true', '-var', "image_tag=$ImageTag")
} else {
    Write-Host 'deploy_apps = false (base infrastructure; removes the app and job if they exist)' -ForegroundColor Cyan
    $planArgs += @('-var', 'deploy_apps=false')
}

Push-Location $InfraRoot
try {
    Invoke-Native { terraform @planArgs }
}
finally { Pop-Location }

Write-Host ''
Write-Host "Plan saved to $PlanFile. Review it above, then run ./scripts/apply.ps1" -ForegroundColor Green
