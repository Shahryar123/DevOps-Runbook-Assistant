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
        Set-VaultSecret -Name $name -PlainValue $plain
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
    }
    finally { $key = $null }
}

Write-Host ''
Write-Host 'Done. A running app reads secrets at start-up; after changing a value, restart it:' -ForegroundColor Green
Write-Host '  az containerapp revision restart -n <app_name> -g <resource_group> --revision <latest revision>'
