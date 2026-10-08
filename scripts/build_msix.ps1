[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Version,
    [string]$DistributionPath = "dist\InferBridge",
    [string]$OutputDirectory = "",
    [string]$IdentityName = $env:INFERBRIDGE_MSIX_IDENTITY_NAME,
    [string]$Publisher = $env:INFERBRIDGE_MSIX_PUBLISHER,
    [string]$PublisherDisplayName = $env:INFERBRIDGE_MSIX_PUBLISHER_DISPLAY_NAME,
    [string]$Python = "python"
)

# Packs the installed-mode PyInstaller layout into an unsigned MSIX for Microsoft Store
# submission. The Store re-signs the package with a Microsoft certificate after
# certification, so no certificate is involved here. See docs/MICROSOFT_STORE.md.

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $Root

foreach ($Pair in @(@("IdentityName", $IdentityName), @("Publisher", $Publisher), @("PublisherDisplayName", $PublisherDisplayName))) {
    if ([string]::IsNullOrWhiteSpace($Pair[1])) {
        throw "-$($Pair[0]) is required. Copy it from Partner Center > Product identity."
    }
}
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $Root "artifacts\release-$Version" }
$Stage = Join-Path $Root "build\msix"
Remove-Item $Stage -Recurse -Force -ErrorAction SilentlyContinue

$Mapping = (& $Python scripts/msix_package.py --distribution $DistributionPath --output-dir $Stage `
    --identity-name $IdentityName --publisher $Publisher --publisher-display-name $PublisherDisplayName `
    --version $Version | Select-Object -Last 1)
if ($LASTEXITCODE -ne 0) { throw "MSIX staging failed." }

$MakeAppx = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\makeappx.exe" -ErrorAction SilentlyContinue |
    Sort-Object { [version]$_.Directory.Parent.Name } -Descending | Select-Object -First 1
if (-not $MakeAppx) { throw "makeappx.exe was not found. Install the Windows 10/11 SDK." }

New-Item $OutputDirectory -ItemType Directory -Force | Out-Null
$Package = Join-Path $OutputDirectory "InferBridge-$Version-windows-x64.msix"
& $MakeAppx.FullName pack /o /f $Mapping /p $Package | Out-Null
if ($LASTEXITCODE -ne 0 -or -not (Test-Path $Package)) { throw "makeappx pack failed." }
Write-Host "Store package: $Package"
