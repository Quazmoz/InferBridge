[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Version,
    [string]$DistributionPath = "dist\InferBridge",
    [string]$NativePublishDirectory = "",
    [string]$OutputDirectory = "",
    [string]$IdentityName = $env:INFERBRIDGE_MSIX_IDENTITY_NAME,
    [string]$Publisher = $env:INFERBRIDGE_MSIX_PUBLISHER,
    [string]$PublisherDisplayName = $env:INFERBRIDGE_MSIX_PUBLISHER_DISPLAY_NAME,
    [string]$Python = "python"
)

# Packs the native WinUI client (native\, the application entrypoint) and the installed-mode
# PyInstaller engine into an unsigned MSIX for Microsoft Store submission. Without
# -NativePublishDirectory the native client is published here with the .NET 10 SDK.
# The Store re-signs the package with a Microsoft certificate after certification,
# so no certificate is involved here. See docs/MICROSOFT_STORE.md.

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $Root

foreach ($Pair in @(@("IdentityName", $IdentityName), @("Publisher", $Publisher), @("PublisherDisplayName", $PublisherDisplayName))) {
    if ([string]::IsNullOrWhiteSpace($Pair[1])) {
        throw "-$($Pair[0]) is required. Copy it from Partner Center > Product identity."
    }
}
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $Root "artifacts\release-$Version" }
if (-not $NativePublishDirectory) {
    # Reuse the actual native entrypoint embedded by the release builder; never
    # silently produce a second different WinUI binary for the Store.
    $BundledNative = Join-Path $DistributionPath "native"
    if (Test-Path -LiteralPath $BundledNative -PathType Container) {
        $NativePublishDirectory = $BundledNative
    } else {
        # Standalone Store packaging may start from an engine-only distribution.
        $NativePublishDirectory = Join-Path $Root "build\native-win-x64"
        Remove-Item $NativePublishDirectory -Recurse -Force -ErrorAction SilentlyContinue
        & dotnet publish windows-native\InferBridge.Native\InferBridge.Native.csproj --configuration Release `
            --runtime win-x64 --self-contained true -p:Platform=x64 "-p:Version=$Version" -o $NativePublishDirectory
        if ($LASTEXITCODE -ne 0) { throw "Native client publish failed. Install the .NET 10 SDK." }
    }
}
$Stage = Join-Path $Root "build\msix"
Remove-Item $Stage -Recurse -Force -ErrorAction SilentlyContinue

$StageArguments = @(
    "scripts/msix_package.py", "--distribution", $DistributionPath, "--output-dir", $Stage,
    "--identity-name", $IdentityName, "--publisher", $Publisher,
    "--publisher-display-name", $PublisherDisplayName, "--version", $Version,
    "--native-publish-dir", $NativePublishDirectory
)
$Mapping = (& $Python @StageArguments | Select-Object -Last 1)
if ($LASTEXITCODE -ne 0) { throw "MSIX staging failed." }

$MakeAppx = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\makeappx.exe" -ErrorAction SilentlyContinue |
    Sort-Object { [version]$_.Directory.Parent.Name } -Descending | Select-Object -First 1
if (-not $MakeAppx) { throw "makeappx.exe was not found. Install the Windows 10/11 SDK." }

# Index the scaled logos so Windows picks the sharpest one for each display and
# shows unplated taskbar icons. Only the manifest and Assets\ are under the layout.
$MakePri = Join-Path $MakeAppx.Directory.FullName "makepri.exe"
if (-not (Test-Path -LiteralPath $MakePri -PathType Leaf)) { throw "makepri.exe was not found beside makeappx.exe." }
$Layout = Join-Path $Stage "layout"
$PriConfig = Join-Path $Stage "priconfig.xml"
$PriOutput = & $MakePri createconfig /cf $PriConfig /dq en-US /pv 10.0.0 /o
if ($LASTEXITCODE -ne 0) { $PriOutput | Write-Host; throw "makepri createconfig failed." }
$PriOutput = & $MakePri new /pr $Layout /cf $PriConfig /mn (Join-Path $Layout "AppxManifest.xml") /of (Join-Path $Layout "resources.pri") /o
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath (Join-Path $Layout "resources.pri"))) { $PriOutput | Write-Host; throw "makepri new failed." }

New-Item $OutputDirectory -ItemType Directory -Force | Out-Null
$Package = Join-Path $OutputDirectory "InferBridge-$Version-windows-x64.msix"
$PackOutput = & $MakeAppx.FullName pack /o /f $Mapping /p $Package
if ($LASTEXITCODE -ne 0 -or -not (Test-Path $Package)) { $PackOutput | Write-Host; throw "makeappx pack failed." }
Write-Host "Store package: $Package"
