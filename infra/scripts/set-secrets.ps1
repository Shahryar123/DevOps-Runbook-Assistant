<#
.SYNOPSIS
  Write the API keys into Key Vault without ever echoing or logging them.

.DESCRIPTION
  Prompts for each secret with Read-Host -AsSecureString (input hidden).
  Leave a prompt empty to keep the current value. Each value is written to a
  temporary file (readable only by you, in your temp folder) and passed with
  --file, so it never appears on a command line, in the process list or in
  shell history; the file is deleted right after.

  Needs: stage 1 applied (the vault exists) and Key Vault Secrets Officer
  (granted by Terraform to owner_object_id / you).

  Container Apps caches Key Vault values (refreshed about every 30 minutes).
  So when a value changed and the app and job exist, the script re-applies
  their secret references (same as Terraform's) and restarts the app, so the
  new value is used right away.

.PARAMETER IncludeSearchAdminKey
  Fallback only (search_local_auth_enabled = true): copy the Search admin key
  into the vault as search-admin-key, straight from Azure, without showing it.

.EXAMPLE
  ./scripts/set-secrets.ps1
#>
param([switch] $IncludeSearchAdminKey)

. "$PSScriptRoot/_common.ps1"
Assert-AzLogin | Out-Null

$vault = Get-TfOutput 'key_vault_name'
$secretNames = @('openai-api-key', 'langsmith-api-key')

# Plausibility checks, so a bad paste is rejected instead of stored. (Pasting
# into a hidden prompt in some terminals, e.g. VS Code with Windows
# PowerShell, can keep only the first character.)
$expected = @{
    'openai-api-key'    = @{ Prefix = 'sk-';   MinLength = 40 }
    'langsmith-api-key' = @{ Prefix = 'lsv2_'; MinLength = 40 }
}

function Test-SecretValue {
    <# Returns $null if the value looks right, else a reason (never the value). #>
    param([string] $Name, [string] $PlainValue)
    $rule = $expected[$Name]
    if ($PlainValue -match '\s') { return 'it contains spaces or line breaks' }
    if ($PlainValue -match '[="'']') { return "it contains quotes or '=' (paste only the key itself)" }
    if ($rule -and -not $PlainValue.StartsWith($rule.Prefix)) { return "it doesn't start with '$($rule.Prefix)'" }
    if ($rule -and $PlainValue.Length -lt $rule.MinLength) {
        return "it's only $($PlainValue.Length) characters (the paste may have been cut off)"
    }
    return $null
}

function Set-VaultSecret {
    <# Write one value to Key Vault through a temp file. #>
    param([string] $Name, [string] $PlainValue)
    $tmp = [System.IO.Path]::GetTempFileName()
    try {
        [System.IO.File]::WriteAllText($tmp, $PlainValue, (New-Object System.Text.UTF8Encoding $false))
        Invoke-Native { az keyvault secret set --vault-name $vault --name $Name `
            --file $tmp --encoding utf-8 --output none } | Out-Null
        Write-Host "  $Name updated." -ForegroundColor Green
    }
    finally {
        Remove-Item -Path $tmp -Force -ErrorAction SilentlyContinue
    }
}

$changed = @()   # secret names written in this run
Write-Host "Key Vault: $vault (input is hidden; empty = keep current value)" -ForegroundColor Cyan
foreach ($name in $secretNames) {
    $secure = Read-Host -AsSecureString "Value for $name"
    if ($secure.Length -eq 0) {
        Write-Host "  $name skipped."
        continue
    }
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
        $problem = Test-SecretValue -Name $name -PlainValue $plain
        if ($problem) {
            throw "Not saved: the value for $name looks wrong because $problem. Nothing was changed for it; run the script again (try a different terminal if pasting fails)."
        }
        Set-VaultSecret -Name $name -PlainValue $plain
        $changed += $name
        Write-Host "    ($($plain.Length) characters, starts with '$($expected[$name].Prefix)')" -ForegroundColor DarkGray
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
        $plain = $null
    }
}

if ($IncludeSearchAdminKey) {
    $searchName = Get-TfOutput 'search_service_name'
    $rg = Get-TfOutput 'resource_group_name'
    $key = Invoke-Native { az search admin-key show --service-name $searchName --resource-group $rg --query primaryKey --output tsv }
    try {
        Set-VaultSecret -Name 'search-admin-key' -PlainValue $key
        $changed += 'search-admin-key'
    }
    finally { $key = $null }
}

# Container Apps caches Key Vault references and re-reads them only about
# every 30 minutes. If the app and job exist, re-apply the same references
# (identical to Terraform's, so no drift) to fetch the new values now, and
# restart the app so its running replica gets them.
if ($changed.Count -gt 0) {
    $app = (Invoke-NativeQuiet { terraform -chdir="$InfraRoot" output -raw app_name }).Output
    $job = (Invoke-NativeQuiet { terraform -chdir="$InfraRoot" output -raw job_name }).Output
    if ($app -and $job) {
        $rg = Get-TfOutput 'resource_group_name'
        $kvUri = "https://$vault.vault.azure.net"
        $targets = @(
            @{ Kind = 'job'; Name = "$job" },
            @{ Kind = 'app'; Name = "$app" }
        )
        foreach ($t in $targets) {
            # The user-assigned identity attached to the app/job (its resource ID).
            $json = if ($t.Kind -eq 'job') {
                Invoke-Native { az containerapp job show -n $t.Name -g $rg --query identity.userAssignedIdentities -o json }
            } else {
                Invoke-Native { az containerapp show -n $t.Name -g $rg --query identity.userAssignedIdentities -o json }
            }
            $identity = (($json -join "`n") | ConvertFrom-Json).PSObject.Properties.Name | Select-Object -First 1
            $refs = @($changed | ForEach-Object { "$_=keyvaultref:$kvUri/secrets/$_,identityref:$identity" })
            Write-Host "Refreshing secret references on the $($t.Kind) $($t.Name) ..."
            if ($t.Kind -eq 'job') {
                Invoke-Native { az containerapp job secret set -n $t.Name -g $rg --secrets @refs --output none } | Out-Null
            } else {
                Invoke-Native { az containerapp secret set -n $t.Name -g $rg --secrets @refs --output none } | Out-Null
                $rev = Invoke-Native { az containerapp show -n $t.Name -g $rg --query properties.latestRevisionName -o tsv }
                Invoke-Native { az containerapp revision restart -n $t.Name -g $rg --revision $rev --output none } | Out-Null
                Write-Host "  restarted revision $rev"
            }
        }
    }
}

Write-Host ''
Write-Host 'Done.' -ForegroundColor Green
