<#
.SYNOPSIS
  Build the image in Azure (ACR Tasks) and tag it with the git commit SHA.

.DESCRIPTION
  az acr build uploads the project folder (respecting .dockerignore), builds
  the Dockerfile in Azure and pushes runbook-assistant:<tag> to the registry,
  so no local Docker is needed. Immutable tags (the SHA, never 'latest') mean
  every deploy names exactly one build and a rollback is just the old tag.

.EXAMPLE
  ./scripts/build-image.ps1              # tag = current git SHA
  ./scripts/build-image.ps1 -Tag test1   # explicit tag (e.g. before the first commit)
#>
param([string] $Tag)

. "$PSScriptRoot/_common.ps1"
Assert-AzLogin | Out-Null

if (-not $Tag) { $Tag = Get-GitSha }
$registry = Get-TfOutput 'acr_name'
$repository = (Read-HclValues $VarFile)['image_repository']
if (-not $repository) { $repository = 'runbook-assistant' }
$image = "${repository}:$Tag"

Write-Host "Building $image in $registry ..." -ForegroundColor Cyan
Invoke-Native { az acr build --registry $registry --image $image `
    --file (Join-Path $ProjectRoot 'Dockerfile') $ProjectRoot }

Write-Host ''
Write-Host "Pushed $image. Deploy it with: ./scripts/plan.ps1 -DeployApps -ImageTag $Tag" -ForegroundColor Green
