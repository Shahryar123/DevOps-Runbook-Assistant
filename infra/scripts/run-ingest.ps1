<#
.SYNOPSIS
  Start the ingest job, follow its logs, and wait for the result.

.DESCRIPTION
  Starts one execution of the Container Apps Job (parallelism 1). Don't start
  another while one is running: Azure AI Search has no lock file, so two
  ingests would write at the same time. Exits non-zero if the execution
  doesn't succeed.

.EXAMPLE
  ./scripts/run-ingest.ps1
#>
param([int] $TimeoutMinutes = 35)

. "$PSScriptRoot/_common.ps1"
Assert-AzLogin | Out-Null

$job = Get-TfOutput 'job_name'
$rg = Get-TfOutput 'resource_group_name'

function Get-ExecutionStatus {
    param([string] $Execution)
    return Invoke-Native { az containerapp job execution show --name $job --resource-group $rg `
        --job-execution-name $Execution --query properties.status --output tsv }
}

# Refuse to start a second writer.
$running = Invoke-Native { az containerapp job execution list --name $job --resource-group $rg `
    --query "[?properties.status=='Running'].name" --output tsv }
if ($running) {
    throw "An ingest execution is already running: $running. Wait for it to finish."
}

$execution = Invoke-Native { az containerapp job start --name $job --resource-group $rg --query name --output tsv }
Write-Host "Started execution $execution" -ForegroundColor Cyan

$deadline = (Get-Date).AddMinutes($TimeoutMinutes)
$finished = @('Succeeded', 'Failed', 'Stopped', 'Degraded')
$logAttempts = 0
$status = ''
while ((Get-Date) -lt $deadline) {
    $status = Get-ExecutionStatus $execution
    if ($status -in $finished) { break }

    if ($status -eq 'Running' -and $logAttempts -lt 3) {
        # Stream the container's console output until it exits. The stream
        # can need a few seconds after start; retry a few times, then fall
        # back to polling the status.
        $logAttempts++
        Write-Host '--- ingest logs ---' -ForegroundColor DarkGray
        $previous = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        & az containerapp job logs show --name $job --resource-group $rg `
            --execution $execution --container ingest --follow --format text
        $streamed = ($LASTEXITCODE -eq 0)
        $ErrorActionPreference = $previous
        Write-Host '--- end of logs ---' -ForegroundColor DarkGray
        if ($streamed) { $logAttempts = 3 } else { Write-Warning 'Log stream not available yet.' }
    } else {
        Write-Host "  status: $status"
    }
    Start-Sleep -Seconds 10
}

Write-Host ''
if ($status -eq 'Succeeded') {
    Write-Host "Ingest $execution succeeded." -ForegroundColor Green
    exit 0
}
Write-Host "Ingest $execution ended with status '$status'." -ForegroundColor Red
Write-Host 'Full logs (Log Analytics, KQL):'
Write-Host "  ContainerAppConsoleLogs_CL | where ContainerJobName_s == '$job' | order by TimeGenerated asc"
exit 1
