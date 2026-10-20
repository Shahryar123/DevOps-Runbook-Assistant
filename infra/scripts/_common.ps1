# Shared helpers for the infra scripts. Dot-source it: . "$PSScriptRoot/_common.ps1"
#
# Stop on the first error. $ErrorActionPreference covers PowerShell cmdlets;
# native tools (az, terraform, git) only set $LASTEXITCODE, so every native
# call goes through Invoke-Native { ... }, which throws on a non-zero exit code.

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$InfraRoot   = Split-Path -Parent $PSScriptRoot           # .../infra
$ProjectRoot = Split-Path -Parent $InfraRoot              # .../runbook-assistant
$EnvName     = 'dev'
$EnvDir      = Join-Path $InfraRoot "environments/$EnvName"
$BackendFile = Join-Path $EnvDir 'backend.hcl'
$VarFile     = Join-Path $EnvDir "$EnvName.tfvars"
$PlanFile    = Join-Path $InfraRoot 'tfplan'

function Invoke-Native {
    <# Run a native command given as a script block; throw if it fails.
         Invoke-Native { az group exists --name $rg }
       Inside the block the command line is parsed normally, so --flags and
       -var=... reach the tool untouched. Stdout streams through (capture it
       with $x = Invoke-Native { ... }). Variable names are unusual so they
       can't shadow the caller's variables used inside the block. #>
    param([Parameter(Mandatory)] [scriptblock] $__NativeBlock)
    & $__NativeBlock
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $($__NativeBlock.ToString().Trim())"
    }
}

function Invoke-NativeQuiet {
    <# Like Invoke-Native, for a command that is allowed to fail: stderr is
       discarded and nothing is thrown. Returns Output and ExitCode.
       (Windows PowerShell 5.1 turns redirected stderr into errors, which
       'Stop' would make fatal, so it's relaxed for this call.) #>
    param([Parameter(Mandatory)] [scriptblock] $__NativeBlock)
    $__previousPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $__output = & $__NativeBlock 2>$null
        $__exitCode = $LASTEXITCODE
    }
    finally { $ErrorActionPreference = $__previousPreference }
    return [pscustomobject]@{ Output = $__output; ExitCode = $__exitCode }
}

function Assert-Tool {
    param([Parameter(Mandatory)] [string] $Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "'$Name' was not found on PATH. Install it first."
    }
}

function Read-HclValues {
    <# Parse simple `key = "value"` / `key = true` lines (backend.hcl, tfvars). #>
    param([Parameter(Mandatory)] [string] $Path)
    $values = @{}
    foreach ($line in Get-Content -Path $Path) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"?([^"#]*?)"?\s*(#.*)?$') {
            $values[$Matches[1]] = $Matches[2].Trim()
        }
    }
    return $values
}

function Assert-AzLogin {
    <# Signed in with az, and on the subscription named in dev.tfvars. #>
    Assert-Tool 'az'
    $account = Invoke-Native { az account show --output json } | ConvertFrom-Json
    $wanted = (Read-HclValues $VarFile)['subscription_id']
    if (-not $wanted -or $wanted -eq '00000000-0000-0000-0000-000000000000') {
        throw "Set subscription_id in $VarFile first."
    }
    if ($account.id -ne $wanted) {
        throw "az is on subscription '$($account.name)' ($($account.id)), but dev.tfvars says $wanted. Run: az account set --subscription $wanted"
    }
    Write-Host "Subscription: $($account.name) ($($account.id))" -ForegroundColor Cyan
    return $account
}

function Get-TfOutput {
    <# Read one Terraform output (raw string). Fails if it's null/missing. #>
    param([Parameter(Mandatory)] [string] $Name)
    Push-Location $InfraRoot
    try {
        $result = Invoke-NativeQuiet { terraform output -raw $Name }
        if ($result.ExitCode -ne 0 -or -not $result.Output) {
            throw "Terraform output '$Name' is empty. Has the right stage been applied (deploy_apps)?"
        }
        return ($result.Output -join '')
    }
    finally { Pop-Location }
}

function Get-GitSha {
    <# Short commit SHA of the project, used as the immutable image tag. #>
    Assert-Tool 'git'
    Push-Location $ProjectRoot
    try {
        $result = Invoke-NativeQuiet { git rev-parse --short=12 HEAD }
        if ($result.ExitCode -ne 0 -or -not $result.Output) {
            throw "Not a git repository (or no commits yet). Commit first, or pass -Tag / -ImageTag explicitly."
        }
        $sha = "$($result.Output)".Trim()
        $dirty = Invoke-Native { git status --porcelain }
        if ($dirty) {
            Write-Warning "Uncommitted changes: the image will contain them, but the tag $sha won't."
        }
        return $sha
    }
    finally { Pop-Location }
}

function Confirm-Typed {
    <# Ask the user to type an exact word; throw if they don't. #>
    param(
        [Parameter(Mandatory)] [string] $Expected,
        [Parameter(Mandatory)] [string] $Prompt
    )
    $answer = Read-Host "$Prompt (type '$Expected' to continue)"
    if ($answer -cne $Expected) {
        throw 'Cancelled: confirmation did not match.'
    }
}
