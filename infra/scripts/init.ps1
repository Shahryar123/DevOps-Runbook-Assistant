<#
.SYNOPSIS
  terraform init with the dev backend (downloads providers, connects state).
.EXAMPLE
  ./scripts/init.ps1
  ./scripts/init.ps1 -Upgrade   # allow newer provider versions within the pins
#>
param([switch] $Upgrade)

. "$PSScriptRoot/_common.ps1"
Assert-Tool 'terraform'

Push-Location $InfraRoot
try {
    $initArgs = @('init', '-input=false', "-backend-config=$BackendFile")
    if ($Upgrade) { $initArgs += '-upgrade' }
    Invoke-Native { terraform @initArgs }
}
finally { Pop-Location }
