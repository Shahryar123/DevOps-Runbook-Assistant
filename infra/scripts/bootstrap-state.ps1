<#
.SYNOPSIS
  Create the Terraform state backend (once). Safe to re-run.

.DESCRIPTION
  Creates, in its own resource group (so destroying the app never touches
  state):
    - a storage account: TLS 1.2+, HTTPS only, no public blob access,
      shared key (account key) access disabled, blob versioning and soft
      delete for blobs and containers
    - the blob container for state files
    - Storage Blob Data Contributor for you (Terraform reads/writes state
      with your Entra ID login: use_azuread_auth = true)
  Names come from environments/dev/backend.hcl (one source of truth).
  Existing resources are left in place; the security settings are re-applied.

.EXAMPLE
  ./scripts/bootstrap-state.ps1 -Location westeurope
#>
param(
    [string] $Location = 'westeurope'
)

. "$PSScriptRoot/_common.ps1"

$account = Assert-AzLogin
$backend = Read-HclValues $BackendFile
$rg        = $backend['resource_group_name']
$sa        = $backend['storage_account_name']
$container = $backend['container_name']
$tags = @('app=runbook-assistant', 'purpose=terraform-state', 'managedBy=bootstrap-script')

Write-Host "State backend: $rg / $sa / $container" -ForegroundColor Cyan

# 1. Resource group
if ((Invoke-Native { az group exists --name $rg }) -eq 'false') {
    Write-Host "Creating resource group $rg ..."
    Invoke-Native { az group create --name $rg --location $Location --tags @tags --output none } | Out-Null
} else {
    Write-Host "Resource group $rg exists."
}

# 2. Storage account
$existing = Invoke-Native { az storage account list --resource-group $rg --query "[?name=='$sa'].id" --output tsv }
$security = @(
    '--min-tls-version', 'TLS1_2',
    '--https-only', 'true',
    '--allow-blob-public-access', 'false',
    '--allow-shared-key-access', 'false'
)
if (-not $existing) {
    $check = Invoke-Native { az storage account check-name --name $sa --output json } | ConvertFrom-Json
    if (-not $check.nameAvailable) {
        throw "Storage account name '$sa' is not available ($($check.reason)). Change storage_account_name in $BackendFile."
    }
    Write-Host "Creating storage account $sa ..."
    Invoke-Native { az storage account create --name $sa --resource-group $rg --location $Location `
        --sku Standard_LRS --kind StorageV2 @security --tags @tags --output none } | Out-Null
} else {
    Write-Host "Storage account $sa exists; re-applying security settings ..."
    Invoke-Native { az storage account update --name $sa --resource-group $rg @security --output none } | Out-Null
}
$saId = Invoke-Native { az storage account show --name $sa --resource-group $rg --query id --output tsv }

# 3. Versioning (every state write keeps the previous version) and soft
#    delete (deleted blobs/containers are recoverable for 14 days).
Write-Host 'Enabling blob versioning and soft delete ...'
Invoke-Native { az storage account blob-service-properties update --account-name $sa --resource-group $rg `
    --enable-versioning true `
    --enable-delete-retention true --delete-retention-days 14 `
    --enable-container-delete-retention true --container-delete-retention-days 14 `
    --output none } | Out-Null

# 4. Data-plane role for you (shared keys are off, so this is the only way in).
$me = Invoke-Native { az ad signed-in-user show --query id --output tsv }
$role = 'Storage Blob Data Contributor'
# Count in PowerShell, not with a JMESPath like length(@): on Windows `az` is
# a .cmd file and cmd.exe misreads the parentheses. The extra pipeline
# unrolls the array (Windows PowerShell 5.1 returns a JSON array as one object).
$json = Invoke-Native { az role assignment list --assignee $me --scope $saId --role $role --output json }
$assignments = @(($json -join "`n" | ConvertFrom-Json) | Where-Object { $_ })
if ($assignments.Count -eq 0) {
    Write-Host "Granting '$role' to you ..."
    Invoke-Native { az role assignment create --assignee-object-id $me --assignee-principal-type User `
        --role $role --scope $saId --output none } | Out-Null
} else {
    Write-Host "You already have '$role'."
}

# 5. Container. Uses your Entra ID login; the new role can take a few
#    minutes to apply, so retry.
$maxAttempts = 12
for ($attempt = 1; $attempt -le $maxAttempts; $attempt++) {
    $result = Invoke-NativeQuiet { az storage container exists --name $container --account-name $sa --auth-mode login --query exists --output tsv }
    if ($result.ExitCode -eq 0) {
        if ("$($result.Output)".Trim() -eq 'true') {
            Write-Host "Container $container exists."
        } else {
            Write-Host "Creating container $container ..."
            Invoke-Native { az storage container create --name $container --account-name $sa --auth-mode login --output none } | Out-Null
        }
        break
    }
    if ($attempt -eq $maxAttempts) {
        throw 'Still no data access to the storage account. Wait a few minutes for the role assignment and re-run this script.'
    }
    Write-Host "Waiting for the role assignment to take effect ($attempt/$maxAttempts) ..."
    Start-Sleep -Seconds 20
}

Write-Host ''
Write-Host "Done. Subscription $($account.id). Next: ./scripts/init.ps1" -ForegroundColor Green
