[CmdletBinding()]
param(
    [switch]$Clean,
    [switch]$ResetEnvironment,
    [switch]$SkipSmokeTest,
    [string]$OutputDirectory = "",
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $Root

function Invoke-Checked([string]$Label, [scriptblock]$Command) {
    Write-Host "==> $Label"
    $global:LASTEXITCODE = 0
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed with exit code $LASTEXITCODE."
    }
}

$Requirements = Join-Path $Root "requirements\release.txt"
$BuildRoot = Join-Path $Root "build\dev-package"
$EnvironmentRoot = Join-Path $Root "build\dev-environments"
$WorkRoot = Join-Path $BuildRoot "pyinstaller"
$DistRoot = if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    Join-Path $BuildRoot "dist"
}
elseif ([IO.Path]::IsPathRooted($OutputDirectory)) {
    [IO.Path]::GetFullPath($OutputDirectory)
}
else {
    [IO.Path]::GetFullPath((Join-Path $Root $OutputDirectory))
}

if ($Clean) {
    Remove-Item $WorkRoot, $DistRoot -Recurse -Force -ErrorAction SilentlyContinue
}
New-Item $BuildRoot, $EnvironmentRoot, $WorkRoot, $DistRoot -ItemType Directory -Force | Out-Null

$EnvironmentKey = (& $Python scripts/release_environment.py fingerprint --requirements $Requirements).Trim()
if ($LASTEXITCODE -ne 0 -or $EnvironmentKey -notmatch '^[a-zA-Z0-9_.-]+$') {
    throw "Could not fingerprint the pinned development packaging environment."
}
$Venv = Join-Path $EnvironmentRoot $EnvironmentKey
$PackagePython = Join-Path $Venv "Scripts\python.exe"
$EnvironmentMetadata = Join-Path $Venv ".inferbridge-release-environment.json"

if ($ResetEnvironment -and (Test-Path $Venv)) {
    Remove-Item $Venv -Recurse -Force
}

$EnvironmentValid = $false
if (Test-Path $PackagePython) {
    & $PackagePython scripts/release_environment.py validate --requirements $Requirements --metadata $EnvironmentMetadata
    if ($LASTEXITCODE -eq 0) {
        & $PackagePython -m pip check
        $EnvironmentValid = $LASTEXITCODE -eq 0
    }
    if (-not $EnvironmentValid) {
        Write-Warning "The cached development packaging environment is invalid and will be rebuilt."
        Remove-Item $Venv -Recurse -Force -ErrorAction SilentlyContinue
    }
}

if (-not $EnvironmentValid) {
    Invoke-Checked "Create fingerprinted development packaging environment" {
        & $Python -m venv $Venv
    }
    Invoke-Checked "Install pinned packaging dependencies" {
        & $PackagePython -m pip install --disable-pip-version-check -r $Requirements
    }
    Invoke-Checked "Validate packaging dependencies" {
        & $PackagePython -m pip check
    }
    Invoke-Checked "Record packaging environment fingerprint" {
        & $PackagePython scripts/release_environment.py write-metadata --requirements $Requirements --metadata $EnvironmentMetadata
    }
}
else {
    Write-Host "Reusing validated development packaging environment: $EnvironmentKey"
}

Invoke-Checked "Install current source without dependency re-resolution" {
    & $PackagePython -m pip install --disable-pip-version-check --no-deps --no-build-isolation .
}

$Version = (& $PackagePython scripts/release_tools.py canonical-version).Trim()
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($Version)) {
    throw "Could not read the canonical application version."
}
$GitCommit = (& git rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $GitCommit -notmatch '^[0-9a-f]{40}$') {
    throw "A Git commit SHA is required for a development package."
}
$TreeClean = [string]::IsNullOrWhiteSpace(((& git status --porcelain) -join "`n"))

$InventoryJson = Join-Path $BuildRoot "dependency-inventory.json"
$Notices = Join-Path $BuildRoot "THIRD-PARTY-NOTICES.txt"
$VersionInfo = Join-Path $BuildRoot "version_info.txt"
$BuildInfo = Join-Path $BuildRoot "build-info.json"
$BrandAssets = Join-Path $BuildRoot "brand"

Invoke-Checked "Generate dependency inventory" {
    & $PackagePython -m pip list --format=json | Set-Content -Path $InventoryJson -Encoding utf8
}
Invoke-Checked "Collect third-party licenses" {
    & $PackagePython -m piplicenses --format=plain-vertical --with-license-file --no-license-path --output-file=$Notices
}
Invoke-Checked "Generate executable version metadata" {
    & $PackagePython scripts/release_tools.py write-version-info --path $VersionInfo --version $Version
}
Invoke-Checked "Generate development build metadata" {
    & $PackagePython scripts/release_tools.py write-build-info --path $BuildInfo --version $Version --channel nightly --commit $GitCommit --clean ($TreeClean.ToString().ToLowerInvariant()) --dependency-inventory $InventoryJson
}
Invoke-Checked "Generate application icons" {
    & $PackagePython scripts/generate_brand_assets.py --output-directory $BrandAssets
}
$AppIcon = Join-Path $BrandAssets "InferBridge.ico"
if (-not (Test-Path $AppIcon)) {
    throw "Application icon generation did not produce $AppIcon."
}

$env:OV_LLM_THIRD_PARTY_NOTICES = $Notices
$env:OV_LLM_VERSION_INFO = $VersionInfo
$env:OV_LLM_BUILD_INFO = $BuildInfo
$env:OV_LLM_APP_ICON = $AppIcon
try {
    # Deliberately omit PyInstaller --clean so unchanged analysis and collection work
    # can be reused between local builds. Production release builds remain unchanged.
    Invoke-Checked "Build incremental PyInstaller distribution" {
        & $PackagePython -m PyInstaller --noconfirm --workpath $WorkRoot --distpath $DistRoot packaging/openvino_windows_llm.spec
    }
}
finally {
    Remove-Item Env:OV_LLM_THIRD_PARTY_NOTICES, Env:OV_LLM_VERSION_INFO, Env:OV_LLM_BUILD_INFO, Env:OV_LLM_APP_ICON -ErrorAction SilentlyContinue
}

$BuiltRoot = Join-Path $DistRoot "InferBridge"
$Launcher = Join-Path $BuiltRoot "InferBridge.exe"
if (-not (Test-Path $Launcher)) {
    throw "Development package launcher was not produced: $Launcher"
}
Invoke-Checked "Verify packaged native components" {
    & $PackagePython scripts/release_tools.py verify-native --path $BuiltRoot
}

if (-not $SkipSmokeTest) {
    Invoke-Checked "Run installed-mode packaged mock smoke test" {
        & (Join-Path $Root "scripts\smoke_test_packaged.ps1") -DistributionPath $BuiltRoot -Python $PackagePython -ExpectedMode installed
    }
}
else {
    Write-Warning "Packaged mock smoke test was skipped by explicit request."
}

Write-Host "Development package completed: $BuiltRoot"
Write-Host "This output is unsigned and is not a release artifact."
