<#
.SYNOPSIS
  Apply exactly the saved plan (infra/tfplan), after a summary and typed confirmation.

.DESCRIPTION
  Applying the saved plan file guarantees that what changes is exactly what
  you reviewed: if anything changed in Azure or in the state since the plan
  was made, Terraform refuses the stale plan instead of silently doing
  something different. The plan file is deleted after a successful apply.
#>

. "$PSScriptRoot/_common.ps1"
Assert-Tool 'terraform'
Assert-AzLogin | Out-Null

if (-not (Test-Path $PlanFile)) {
    throw "No saved plan at $PlanFile. Run ./scripts/plan.ps1 first."
}

Push-Location $InfraRoot
try {
    # Summary from the machine-readable plan.
    $plan = Invoke-Native { terraform show -json $PlanFile } | ConvertFrom-Json
    $changes = @($plan.resource_changes | Where-Object { $_.change.actions -notcontains 'no-op' -and $_.change.actions -notcontains 'read' })

    $summary = [ordered]@{ create = @(); update = @(); replace = @(); delete = @() }
    foreach ($rc in $changes) {
        $actions = @($rc.change.actions)
        if ($actions -contains 'create' -and $actions -contains 'delete') { $summary.replace += $rc.address }
        elseif ($actions -contains 'create') { $summary.create += $rc.address }
        elseif ($actions -contains 'update') { $summary.update += $rc.address }
        elseif ($actions -contains 'delete') { $summary.delete += $rc.address }
    }

    Write-Host ''
    Write-Host 'Saved plan summary' -ForegroundColor Cyan
    foreach ($kind in $summary.Keys) {
        $color = if ($kind -in @('delete', 'replace')) { 'Yellow' } else { 'Gray' }
        Write-Host ("  {0,-8} {1}" -f $kind, $summary[$kind].Count) -ForegroundColor $color
        foreach ($address in $summary[$kind]) {
            Write-Host "           $address" -ForegroundColor $color
        }
    }
    Write-Host ''

    if ($changes.Count -eq 0) {
        Write-Host 'No resource changes (outputs may still change).'
    }

    Confirm-Typed -Expected 'apply' -Prompt 'Apply this plan'
    Invoke-Native { terraform apply -input=false $PlanFile }
    Remove-Item $PlanFile
}
finally { Pop-Location }

Write-Host ''
Write-Host 'Applied. Outputs: terraform output' -ForegroundColor Green
