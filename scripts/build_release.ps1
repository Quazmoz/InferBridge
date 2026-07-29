[CmdletBinding()]
param(
    [string]$Version = "",
    [ValidateSet("stable", "beta", "nightly")][string]$Channel = "stable",
    [switch]$Clean,
    [switch]$Unsigned,
    [switch]$SkipInstaller,
    [switch]$SkipPortable,
    [switch]$SkipTests,
    [string]$OutputDirectory = "",
    [switch]$MockSmokeTest,
    [switch]$Sign,
    [switch]$GenerateChecksums,
    [switch]$AllowDirty,
    [string]$Python = "python",
    [string]$IsccPath = $env:ISCC_PATH
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $Root

function Invoke-Checked([string]$Label, [scriptblock]$Command) {
    Write-Host "==> $Label"
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "$Label failed with exit code $LASTEXITCODE." }
}

function Resolve-Iscc([string]$Requested) {
    if ($Requested -and (Test-Path $Requested)) { return (Resolve-Path $Requested).Path }
    $Candidates = @(
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles(x86)\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
    )
    return $Candidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
}

function Resolve-SignTool() {
    if ($env:OV_LLM_SIGNTOOL_PATH -and (Test-Path $env:OV_LLM_SIGNTOOL_PATH)) {
        return (Resolve-Path $env:OV_LLM_SIGNTOOL_PATH).Path
    }
    $Found = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if ($Found) { return $Found.Source }
    throw "signtool.exe was not found. Set OV_LLM_SIGNTOOL_PATH."
}

function Sign-AndVerify([string]$Path) {
    $SignTool = Resolve-SignTool
    $Timestamp = if ($env:OV_LLM_SIGN_TIMESTAMP_URL) { $env:OV_LLM_SIGN_TIMESTAMP_URL } else { "http://timestamp.digicert.com" }
    $TimestampUri = $null
    if (-not [Uri]::TryCreate($Timestamp, [UriKind]::Absolute, [ref]$TimestampUri) -or $TimestampUri.Scheme -notin @("http", "https")) {
        throw "OV_LLM_SIGN_TIMESTAMP_URL must be an absolute HTTP(S) RFC 3161 timestamp URL."
    }
    if ($env:OV_LLM_SIGN_CERT_SHA1 -and $env:OV_LLM_SIGN_CERTIFICATE) {
        throw "Configure either OV_LLM_SIGN_CERT_SHA1 or OV_LLM_SIGN_CERTIFICATE, not both."
    }
    $Arguments = @("sign", "/fd", "SHA256", "/tr", $Timestamp, "/td", "SHA256")
    if ($env:OV_LLM_SIGN_CERT_SHA1) {
        $Arguments += @("/sha1", $env:OV_LLM_SIGN_CERT_SHA1)
    }
    elseif ($env:OV_LLM_SIGN_CERTIFICATE) {
        if (-not (Test-Path $env:OV_LLM_SIGN_CERTIFICATE)) { throw "OV_LLM_SIGN_CERTIFICATE does not exist." }
        if ([string]::IsNullOrWhiteSpace($env:OV_LLM_SIGN_CERTIFICATE_PASSWORD)) {
            throw "PFX signing requires OV_LLM_SIGN_CERTIFICATE_PASSWORD from the secure environment."
        }
        $Arguments += @("/f", (Resolve-Path $env:OV_LLM_SIGN_CERTIFICATE).Path)
        $Arguments += @("/p", $env:OV_LLM_SIGN_CERTIFICATE_PASSWORD)
    }
    else {
        throw "Signing was requested but no certificate-store thumbprint or certificate file was configured."
    }
    $Arguments += $Path
    & $SignTool @Arguments | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Authenticode signing failed for $([IO.Path]::GetFileName($Path))." }
    & $SignTool verify /pa /all $Path | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Authenticode verification failed for $([IO.Path]::GetFileName($Path))." }
}

if ($Sign -and $Unsigned) { throw "Use either -Sign or -Unsigned, not both." }
if ($SkipInstaller -and $SkipPortable) { throw "At least one of installer or portable output must be enabled." }
if ($Sign -and ($SkipInstaller -or $SkipPortable)) {
    throw "Signed releases require both the launcher-containing portable ZIP and installer."
}

$CanonicalVersion = (& $Python scripts/release_tools.py canonical-version).Trim()
if ($LASTEXITCODE -ne 0) { throw "Could not read the canonical application version." }
if (-not $Version) { $Version = $CanonicalVersion }
Invoke-Checked "Validate semantic version and channel" { & $Python scripts/release_tools.py validate-version --version $Version --channel $Channel }
Invoke-Checked "Verify canonical version consistency" { & $Python scripts/release_tools.py verify-version-consistency --root $Root --version $Version }
Invoke-Checked "Verify pinned release requirements" { & $Python scripts/release_tools.py verify-requirements --path requirements/release.txt }

$GitCommit = (& git rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $GitCommit -notmatch '^[0-9a-f]{40}$') { throw "A Git commit SHA is required for a release build." }
$DirtyOutput = (& git status --porcelain)
$TreeClean = [string]::IsNullOrWhiteSpace(($DirtyOutput -join "`n"))
if (-not $TreeClean -and -not $AllowDirty) {
    throw "The working tree is dirty. Commit or stash changes, or use -AllowDirty for an explicitly non-release build."
}
if (-not $TreeClean) { Write-Warning "Building from an uncommitted working tree. The manifest will record source_tree_clean=false." }

$BuildRoot = Join-Path $Root "build\release"
$DistRoot = Join-Path $Root "dist"
$Artifacts = if ($OutputDirectory) {
    if ([IO.Path]::IsPathRooted($OutputDirectory)) { [IO.Path]::GetFullPath($OutputDirectory) }
    else { [IO.Path]::GetFullPath((Join-Path $Root $OutputDirectory)) }
} else { Join-Path $Root "artifacts\release-$Version" }
$Venv = Join-Path $BuildRoot "venv"
if ($Clean) {
    Remove-Item $BuildRoot, $DistRoot -Recurse -Force -ErrorAction SilentlyContinue
}
Remove-Item $DistRoot -Recurse -Force -ErrorAction SilentlyContinue
if (-not $OutputDirectory) {
    Remove-Item $Artifacts -Recurse -Force -ErrorAction SilentlyContinue
}
elseif (Test-Path $Artifacts) {
    Get-ChildItem $Artifacts -File -Filter "OpenVINO-Windows-LLM-$Version-*" | Remove-Item -Force
}
New-Item $BuildRoot, $Artifacts -ItemType Directory -Force | Out-Null

if (-not (Test-Path (Join-Path $Venv "Scripts\python.exe"))) {
    Invoke-Checked "Create isolated release environment" { & $Python -m venv $Venv }
}
$ReleasePython = Join-Path $Venv "Scripts\python.exe"
Invoke-Checked "Install pinned release dependencies" { & $ReleasePython -m pip install --disable-pip-version-check -r requirements/release.txt }
Invoke-Checked "Install project without dependency re-resolution" { & $ReleasePython -m pip install --disable-pip-version-check --no-deps --no-build-isolation . }

$InventoryJson = Join-Path $Artifacts "OpenVINO-Windows-LLM-$Version-dependency-inventory.json"
$InventoryText = Join-Path $Artifacts "OpenVINO-Windows-LLM-$Version-dependency-freeze.txt"
& $ReleasePython -m pip list --format=json | Set-Content -Path $InventoryJson -Encoding utf8
if ($LASTEXITCODE -ne 0) { throw "Dependency inventory generation failed." }
& $ReleasePython -m pip freeze --all --exclude openvino-windows-llm | Set-Content -Path $InventoryText -Encoding utf8
if ($LASTEXITCODE -ne 0) { throw "Dependency freeze generation failed." }

if (-not $SkipTests) {
    Invoke-Checked "Ruff lint" { & $ReleasePython -m ruff check . }
    Invoke-Checked "Ruff formatting check" { & $ReleasePython -m ruff format --check . }
    Invoke-Checked "Pytest" { & $ReleasePython -m pytest }
    Invoke-Checked "External mock API contract" {
        & (Join-Path $Root "scripts\validate_mock_contract.ps1") -Python $ReleasePython -OutputDirectory (Join-Path $BuildRoot "mock-contract")
    }
}
else {
    Write-Warning "Tests were skipped by explicit -SkipTests request."
}

$Notices = Join-Path $BuildRoot "THIRD-PARTY-NOTICES.txt"
Invoke-Checked "Collect third-party licenses" { & $ReleasePython -m piplicenses --format=plain-vertical --with-license-file --no-license-path --output-file=$Notices }
$VersionInfo = Join-Path $BuildRoot "version_info.txt"
$BuildInfo = Join-Path $BuildRoot "build-info.json"
Invoke-Checked "Generate executable version metadata" { & $ReleasePython scripts/release_tools.py write-version-info --path $VersionInfo --version $Version }
Invoke-Checked "Generate build metadata" { & $ReleasePython scripts/release_tools.py write-build-info --path $BuildInfo --version $Version --channel $Channel --commit $GitCommit --clean ($TreeClean.ToString().ToLowerInvariant()) --dependency-inventory $InventoryJson }
$BrandAssets = Join-Path $BuildRoot "brand"
Invoke-Checked "Generate application icons" { & $ReleasePython scripts/generate_brand_assets.py --output-directory $BrandAssets }
$AppIcon = Join-Path $BrandAssets "OpenVINOWindowsLLM.ico"
if (-not (Test-Path $AppIcon)) { throw "Application icon generation did not produce $AppIcon." }

Remove-Item $DistRoot -Recurse -Force -ErrorAction SilentlyContinue
$env:OV_LLM_THIRD_PARTY_NOTICES = $Notices
$env:OV_LLM_VERSION_INFO = $VersionInfo
$env:OV_LLM_BUILD_INFO = $BuildInfo
$env:OV_LLM_APP_ICON = $AppIcon
try {
    Invoke-Checked "Build PyInstaller distribution" { & $ReleasePython -m PyInstaller --noconfirm --clean packaging/openvino_windows_llm.spec }
}
finally {
    Remove-Item Env:OV_LLM_THIRD_PARTY_NOTICES, Env:OV_LLM_VERSION_INFO, Env:OV_LLM_BUILD_INFO, Env:OV_LLM_APP_ICON -ErrorAction SilentlyContinue
}

$BuiltRoot = Join-Path $DistRoot "OpenVINOWindowsLLM"
$Launcher = Join-Path $BuiltRoot "OpenVINOWindowsLLM.exe"
Invoke-Checked "Verify packaged native components" { & $ReleasePython scripts/release_tools.py verify-native --path $BuiltRoot }
Invoke-Checked "Scan packaged directory" { & $ReleasePython scripts/release_tools.py scan --path $BuiltRoot }

$RunMockSmoke = (-not [bool]$SkipTests) -or [bool]$MockSmokeTest
if ($RunMockSmoke -and -not $SkipInstaller) {
    if (Test-Path (Join-Path $BuiltRoot "portable.flag")) {
        throw "Installed-mode distribution unexpectedly contains portable.flag."
    }
    Invoke-Checked "Run installed-mode packaged mock smoke test" {
        & (Join-Path $Root "scripts\smoke_test_packaged.ps1") -DistributionPath $BuiltRoot -Python $ReleasePython -ExpectedMode installed
    }
}

$LauncherSigned = $false
if ($Sign) {
    Sign-AndVerify $Launcher
    $LauncherSigned = $true
}
else {
    Write-Warning "Building unsigned artifacts. Use -Sign with secure signing environment variables for a signed release."
}

$Produced = @($InventoryJson, $InventoryText)
$SignedTypes = @()
if (-not $SkipPortable) {
    $PortableContainer = Join-Path $BuildRoot "portable"
    $PortableName = "OpenVINO-Windows-LLM-$Version"
    $PortableStage = Join-Path $PortableContainer $PortableName
    Remove-Item $PortableContainer -Recurse -Force -ErrorAction SilentlyContinue
    New-Item $PortableStage -ItemType Directory -Force | Out-Null
    Copy-Item (Join-Path $BuiltRoot "*") $PortableStage -Recurse -Force
    Set-Content -Path (Join-Path $PortableStage "portable.flag") -Value "portable" -Encoding ascii
    @"
OpenVINO Windows LLM $Version portable release

1. Extract the complete directory to a writable non-administrator location.
2. Run OpenVINOWindowsLLM.exe.
3. Mutable configuration, models, caches, logs, onboarding state, and benchmarks remain under .\data.
4. This package does not change the registry or Start Menu and does not enable Start with Windows.
5. See UPGRADE_ROLLBACK.md before replacing an existing portable directory.
"@ | Set-Content -Path (Join-Path $PortableStage "PORTABLE-README.txt") -Encoding utf8
    Copy-Item docs/UPGRADE_ROLLBACK.md, docs/KNOWN_ISSUES.md, docs/COMPATIBILITY_MATRIX.md -Destination $PortableStage
    $PortableZip = Join-Path $Artifacts "OpenVINO-Windows-LLM-$Version-windows-x64-portable.zip"
    Remove-Item $PortableZip -Force -ErrorAction SilentlyContinue
    Compress-Archive -Path $PortableStage -DestinationPath $PortableZip -CompressionLevel Optimal
    Invoke-Checked "Validate portable ZIP paths" { & $ReleasePython scripts/release_tools.py scan --path $PortableZip }
    $ExtractRoot = Join-Path ([IO.Path]::GetTempPath()) ("OV LLM Portable Smoke " + [guid]::NewGuid().ToString("N"))
    try {
        Expand-Archive -Path $PortableZip -DestinationPath $ExtractRoot
        $ExtractedDistribution = Join-Path $ExtractRoot $PortableName
        if (-not (Test-Path (Join-Path $ExtractedDistribution "portable.flag"))) { throw "Portable marker missing after extraction." }
        if ($RunMockSmoke) {
            Invoke-Checked "Run portable packaged mock smoke test" {
                & (Join-Path $Root "scripts\smoke_test_packaged.ps1") -DistributionPath $ExtractedDistribution -Python $ReleasePython -ExpectedMode portable
            }
        }
    }
    finally {
        Remove-Item $ExtractRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
    $Produced += $PortableZip
    if ($LauncherSigned) { $SignedTypes += "portable" }
}

if (-not $SkipInstaller) {
    $Compiler = Resolve-Iscc $IsccPath
    if (-not $Compiler) { throw "Inno Setup 6 compiler was not found. Set ISCC_PATH or use -SkipInstaller." }
    $CoreVersion = ($Version -split '[-+]')[0]
    $NumericVersion = "$CoreVersion.0"
    Invoke-Checked "Compile Inno Setup installer" {
        & $Compiler "/DMyAppVersion=$Version" "/DMyAppVersionNumeric=$NumericVersion" "/DSourceRoot=$BuiltRoot" "/DArtifactDir=$Artifacts" "/DAppIconPath=$AppIcon" packaging/installer.iss
    }
    $Installer = Join-Path $Artifacts "OpenVINO-Windows-LLM-$Version-windows-x64-installer.exe"
    if (-not (Test-Path $Installer)) { throw "Installer was not produced: $Installer" }
    if ($Sign) {
        Sign-AndVerify $Installer
        $SignedTypes += "installer"
    }
    $Produced += $Installer
}

$LicenseStage = Join-Path $BuildRoot "third-party-licenses"
Remove-Item $LicenseStage -Recurse -Force -ErrorAction SilentlyContinue
New-Item $LicenseStage -ItemType Directory -Force | Out-Null
Copy-Item LICENSE, $Notices, $InventoryJson, $InventoryText, docs/THIRD_PARTY_LICENSES.md -Destination $LicenseStage
$LicenseZip = Join-Path $Artifacts "OpenVINO-Windows-LLM-$Version-third-party-licenses.zip"
Compress-Archive -Path (Join-Path $LicenseStage "*") -DestinationPath $LicenseZip -CompressionLevel Optimal -Force
$Produced += $LicenseZip

$ReleaseNotesSource = Join-Path $Root "docs\releases\$Version.md"
if (-not (Test-Path $ReleaseNotesSource)) { throw "Structured release notes are required at docs/releases/$Version.md." }
$ReleaseNotes = Join-Path $Artifacts "OpenVINO-Windows-LLM-$Version-release-notes.md"
Copy-Item $ReleaseNotesSource $ReleaseNotes -Force
$Produced += $ReleaseNotes

$ModelLibrarySource = Join-Path $Root "model_library_manifest.json"
if (-not (Test-Path $ModelLibrarySource)) { throw "Curated model library manifest is missing at $ModelLibrarySource." }
Invoke-Checked "Validate model library manifest" { & $ReleasePython scripts/validate_model_library_manifest.py $ModelLibrarySource }
$ModelLibraryAsset = Join-Path $Artifacts "model-library-manifest.json"
Copy-Item $ModelLibrarySource $ModelLibraryAsset -Force
$Produced += $ModelLibraryAsset

$PublishedAt = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")
Invoke-Checked "Generate and validate release manifest" {
    & $ReleasePython scripts/release_tools.py manifest --output-dir $Artifacts --version $Version --channel $Channel --published-at $PublishedAt --commit $GitCommit --clean ($TreeClean.ToString().ToLowerInvariant()) "--signed-types=$($SignedTypes -join ',')" --inventory-filename ([IO.Path]::GetFileName($InventoryJson))
}
$Manifest = Join-Path $Artifacts "OpenVINO-Windows-LLM-$Version-release-manifest.json"
$Produced += $Manifest

$SummaryPath = Join-Path $Artifacts "OpenVINO-Windows-LLM-$Version-release-summary.json"
$Summary = [ordered]@{
    schema_version = 1
    version = $Version
    channel = $Channel
    source_commit = $GitCommit
    source_tree_clean = $TreeClean
    tests_skipped = [bool]$SkipTests
    source_mock_contract_validation = -not [bool]$SkipTests
    packaged_mock_smoke_test = $RunMockSmoke
    packaged_installed_mode_smoke_test = $RunMockSmoke -and -not [bool]$SkipInstaller
    packaged_portable_mode_smoke_test = $RunMockSmoke -and -not [bool]$SkipPortable
    launcher_signature_verified = $LauncherSigned
    installer_signature_verified = ($SignedTypes -contains "installer")
    artifact_directory = "."
    artifacts = @($Produced | ForEach-Object { [IO.Path]::GetFileName($_) })
    unverified = @(
        "installer upgrade and downgrade on a real Windows installation",
        "Authenticode signing unless -Sign completed successfully",
        "real Intel CPU execution",
        "real Intel GPU execution",
        "real Intel NPU execution"
    )
}
$Summary | ConvertTo-Json -Depth 8 | Set-Content -Path $SummaryPath -Encoding utf8
$Produced += $SummaryPath

# Checksums are mandatory for every release. -GenerateChecksums remains accepted for explicit scripts.
Invoke-Checked "Generate and verify SHA-256 checksums" { & $ReleasePython scripts/release_tools.py checksums --output-dir $Artifacts --version $Version }
$Checksums = Join-Path $Artifacts "OpenVINO-Windows-LLM-$Version-checksums.txt"
$Produced += $Checksums

foreach ($Artifact in $Produced) {
    Invoke-Checked "Scan $([IO.Path]::GetFileName($Artifact))" { & $ReleasePython scripts/release_tools.py scan --path $Artifact }
}
Invoke-Checked "Re-verify final checksum file" { & $ReleasePython scripts/release_tools.py verify-checksums --path $Checksums }

Write-Host "Release build completed:"
Get-ChildItem $Artifacts -File | Sort-Object Name | ForEach-Object { Write-Host "  $($_.FullName)" }
