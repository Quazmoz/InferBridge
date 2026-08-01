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

$script:ReleaseStartedAt = [DateTime]::UtcNow
$script:ReleaseStopwatch = [Diagnostics.Stopwatch]::StartNew()
$script:ReleaseTimings = [Collections.Generic.List[object]]::new()
$script:TimingSnapshotPath = $null
$script:ReleaseEnvironmentKey = $null
$script:ReleaseEnvironmentReused = $false
$script:ScannedArtifacts = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)

function Write-ReleaseTimingSnapshot([string]$Path, [bool]$Finalized = $false) {
    if ([string]::IsNullOrWhiteSpace($Path)) { return }
    $Directory = Split-Path -Parent $Path
    if ($Directory) { New-Item $Directory -ItemType Directory -Force | Out-Null }
    $Payload = [ordered]@{
        schema_version = 1
        started_at = $script:ReleaseStartedAt.ToString("yyyy-MM-ddTHH:mm:ssZ")
        generated_at = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")
        elapsed_ms = [Math]::Round($script:ReleaseStopwatch.Elapsed.TotalMilliseconds, 3)
        finalized = $Finalized
        environment = [ordered]@{
            key = $script:ReleaseEnvironmentKey
            reused = [bool]$script:ReleaseEnvironmentReused
        }
        steps = @($script:ReleaseTimings)
    }
    $Temporary = "$Path.tmp"
    $Payload | ConvertTo-Json -Depth 8 | Set-Content -Path $Temporary -Encoding utf8
    Move-Item $Temporary $Path -Force
}

function Add-ReleaseTiming([string]$Label, [Diagnostics.Stopwatch]$Stopwatch, [bool]$Succeeded) {
    $Stopwatch.Stop()
    $script:ReleaseTimings.Add([ordered]@{
        label = $Label
        duration_ms = [Math]::Round($Stopwatch.Elapsed.TotalMilliseconds, 3)
        succeeded = $Succeeded
    })
    if ($script:TimingSnapshotPath) {
        Write-ReleaseTimingSnapshot -Path $script:TimingSnapshotPath
    }
}

function Invoke-Checked([string]$Label, [scriptblock]$Command) {
    Write-Host "==> $Label"
    $Stopwatch = [Diagnostics.Stopwatch]::StartNew()
    $Succeeded = $false
    try {
        $global:LASTEXITCODE = 0
        & $Command
        if ($LASTEXITCODE -ne 0) { throw "$Label failed with exit code $LASTEXITCODE." }
        $Succeeded = $true
    }
    finally {
        Add-ReleaseTiming -Label $Label -Stopwatch $Stopwatch -Succeeded $Succeeded
    }
}

function Get-ArtifactScanKey([string]$Path) {
    $Item = Get-Item -LiteralPath $Path
    if ($Item.PSIsContainer) {
        throw "Artifact scan tracking requires a file path: $Path"
    }
    return "$($Item.FullName)|$($Item.Length)|$($Item.LastWriteTimeUtc.Ticks)"
}

function Invoke-ArtifactScan([string]$Path, [string]$Label = "") {
    $Item = Get-Item -LiteralPath $Path
    $ScanKey = Get-ArtifactScanKey -Path $Item.FullName
    if ($script:ScannedArtifacts.Contains($ScanKey)) {
        Write-Host "==> Skip unchanged artifact scan: $($Item.Name)"
        return
    }
    if ([string]::IsNullOrWhiteSpace($Label)) {
        $Label = "Scan $($Item.Name)"
    }
    Invoke-Checked $Label {
        & $ReleasePython scripts/release_tools.py scan --path $Item.FullName
    }
    [void]$script:ScannedArtifacts.Add($ScanKey)
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
$ReleaseEnvironmentRoot = Join-Path $Root "build\release-environments"
$ReleaseRequirements = Join-Path $Root "requirements\release.txt"
$DistRoot = Join-Path $Root "dist"
$Artifacts = if ($OutputDirectory) {
    if ([IO.Path]::IsPathRooted($OutputDirectory)) { [IO.Path]::GetFullPath($OutputDirectory) }
    else { [IO.Path]::GetFullPath((Join-Path $Root $OutputDirectory)) }
} else { Join-Path $Root "artifacts\release-$Version" }
if ($Clean) {
    Remove-Item $BuildRoot, $DistRoot -Recurse -Force -ErrorAction SilentlyContinue
}
Remove-Item $DistRoot -Recurse -Force -ErrorAction SilentlyContinue
if (-not $OutputDirectory) {
    Remove-Item $Artifacts -Recurse -Force -ErrorAction SilentlyContinue
}
elseif (Test-Path $Artifacts) {
    Get-ChildItem $Artifacts -File -Filter "InferBridge-$Version-*" | Remove-Item -Force
}
New-Item $BuildRoot, $ReleaseEnvironmentRoot, $Artifacts -ItemType Directory -Force | Out-Null
$script:TimingSnapshotPath = Join-Path $BuildRoot "release-timings.json"
Write-ReleaseTimingSnapshot -Path $script:TimingSnapshotPath

$EnvironmentKey = (& $Python scripts/release_environment.py fingerprint --requirements $ReleaseRequirements).Trim()
if ($LASTEXITCODE -ne 0 -or $EnvironmentKey -notmatch '^[a-zA-Z0-9_.-]+$') {
    throw "Could not fingerprint the pinned release environment."
}
$script:ReleaseEnvironmentKey = $EnvironmentKey
$Venv = Join-Path $ReleaseEnvironmentRoot $EnvironmentKey
$ReleasePython = Join-Path $Venv "Scripts\python.exe"
$EnvironmentMetadata = Join-Path $Venv ".inferbridge-release-environment.json"
$EnvironmentValid = $false
if (Test-Path $ReleasePython) {
    Write-Host "==> Validate reusable release environment"
    $ProbeStopwatch = [Diagnostics.Stopwatch]::StartNew()
    try {
        & $ReleasePython scripts/release_environment.py validate --requirements $ReleaseRequirements --metadata $EnvironmentMetadata
        if ($LASTEXITCODE -eq 0) {
            & $ReleasePython -m pip check
            $EnvironmentValid = $LASTEXITCODE -eq 0
        }
    }
    finally {
        Add-ReleaseTiming -Label "Validate reusable release environment" -Stopwatch $ProbeStopwatch -Succeeded $EnvironmentValid
    }
    if (-not $EnvironmentValid) {
        Write-Warning "The cached release environment failed validation and will be rebuilt."
        Remove-Item $Venv -Recurse -Force -ErrorAction SilentlyContinue
    }
}

if (-not $EnvironmentValid) {
    Invoke-Checked "Create fingerprinted release environment" { & $Python -m venv $Venv }
    Invoke-Checked "Install pinned release dependencies" { & $ReleasePython -m pip install --disable-pip-version-check -r $ReleaseRequirements }
    Invoke-Checked "Validate installed release dependencies" { & $ReleasePython -m pip check }
    Invoke-Checked "Record release environment fingerprint" {
        & $ReleasePython scripts/release_environment.py write-metadata --requirements $ReleaseRequirements --metadata $EnvironmentMetadata
    }
}
else {
    $script:ReleaseEnvironmentReused = $true
    Write-Host "Reusing validated release environment: $EnvironmentKey"
}
Invoke-Checked "Install project without dependency re-resolution" { & $ReleasePython -m pip install --disable-pip-version-check --no-deps --no-build-isolation . }

$InventoryJson = Join-Path $Artifacts "InferBridge-$Version-dependency-inventory.json"
$InventoryText = Join-Path $Artifacts "InferBridge-$Version-dependency-freeze.txt"
Invoke-Checked "Generate dependency inventory" {
    & $ReleasePython -m pip list --format=json | Set-Content -Path $InventoryJson -Encoding utf8
}
Invoke-Checked "Generate dependency freeze" {
    & $ReleasePython -m pip freeze --all --exclude openvino-windows-llm | Set-Content -Path $InventoryText -Encoding utf8
}

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
$AppIcon = Join-Path $BrandAssets "InferBridge.ico"
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

$BuiltRoot = Join-Path $DistRoot "InferBridge"
$Launcher = Join-Path $BuiltRoot "InferBridge.exe"
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
    Invoke-Checked "Sign and verify packaged launcher" { Sign-AndVerify $Launcher }
    $LauncherSigned = $true
}
else {
    Write-Warning "Building unsigned artifacts. Use -Sign with secure signing environment variables for a signed release."
}

$Produced = @($InventoryJson, $InventoryText)
$SignedTypes = @()

# Build the installed-mode installer before temporarily adding portable-only files.
if (-not $SkipInstaller) {
    $Compiler = Resolve-Iscc $IsccPath
    if (-not $Compiler) { throw "Inno Setup 6 compiler was not found. Set ISCC_PATH or use -SkipInstaller." }
    $CoreVersion = ($Version -split '[-+]')[0]
    $NumericVersion = "$CoreVersion.0"
    Invoke-Checked "Compile Inno Setup installer" {
        & $Compiler "/DMyAppVersion=$Version" "/DMyAppVersionNumeric=$NumericVersion" "/DSourceRoot=$BuiltRoot" "/DArtifactDir=$Artifacts" "/DAppIconPath=$AppIcon" packaging/installer.iss
    }
    $Installer = Join-Path $Artifacts "InferBridge-$Version-windows-x64-installer.exe"
    if (-not (Test-Path $Installer)) { throw "Installer was not produced: $Installer" }
    if ($Sign) {
        Invoke-Checked "Sign and verify installer" { Sign-AndVerify $Installer }
        $SignedTypes += "installer"
    }
    $Produced += $Installer
}

if (-not $SkipPortable) {
    $PortableName = "InferBridge-$Version"
    $PortableMarker = Join-Path $BuiltRoot "portable.flag"
    $PortableReadme = Join-Path $BuiltRoot "PORTABLE-README.txt"
    $PortableDocuments = @(
        @{ Source = "docs/UPGRADE_ROLLBACK.md"; Target = (Join-Path $BuiltRoot "UPGRADE_ROLLBACK.md") },
        @{ Source = "docs/KNOWN_ISSUES.md"; Target = (Join-Path $BuiltRoot "KNOWN_ISSUES.md") },
        @{ Source = "docs/COMPATIBILITY_MATRIX.md"; Target = (Join-Path $BuiltRoot "COMPATIBILITY_MATRIX.md") }
    )
    $PortableOnlyPaths = @($PortableMarker, $PortableReadme) + @($PortableDocuments | ForEach-Object { $_.Target })
    foreach ($Path in $PortableOnlyPaths) {
        if (Test-Path $Path) { throw "Portable staging would overwrite an existing packaged file: $Path" }
    }

    try {
        Set-Content -Path $PortableMarker -Value "portable" -Encoding ascii
        @"
InferBridge $Version portable release

1. Extract the complete directory to a writable non-administrator location.
2. Run InferBridge.exe.
3. Mutable configuration, models, caches, logs, onboarding state, and benchmarks remain under .\data.
4. This package does not change the registry or Start Menu and does not enable Start with Windows.
5. See UPGRADE_ROLLBACK.md before replacing an existing portable directory.
"@ | Set-Content -Path $PortableReadme -Encoding utf8
        foreach ($Document in $PortableDocuments) {
            Copy-Item $Document.Source $Document.Target -Force
        }

        if ($RunMockSmoke) {
            Invoke-Checked "Run portable packaged mock smoke test" {
                & (Join-Path $Root "scripts\smoke_test_packaged.ps1") -DistributionPath $BuiltRoot -Python $ReleasePython -ExpectedMode portable
            }
        }

        $PortableZip = Join-Path $Artifacts "InferBridge-$Version-windows-x64-portable.zip"
        Invoke-Checked "Create portable ZIP without staging copy" {
            & $ReleasePython scripts/create_portable_archive.py create --source-root $BuiltRoot --output $PortableZip --archive-root $PortableName
        }
        Invoke-ArtifactScan -Path $PortableZip -Label "Validate portable ZIP paths"
        Invoke-Checked "Verify portable ZIP layout" {
            & $ReleasePython scripts/create_portable_archive.py verify --path $PortableZip --archive-root $PortableName
        }
    }
    finally {
        Remove-Item $PortableOnlyPaths -Force -ErrorAction SilentlyContinue
    }
    $Produced += $PortableZip
    if ($LauncherSigned) { $SignedTypes += "portable" }
}

$LicenseStage = Join-Path $BuildRoot "third-party-licenses"
Remove-Item $LicenseStage -Recurse -Force -ErrorAction SilentlyContinue
New-Item $LicenseStage -ItemType Directory -Force | Out-Null
Copy-Item LICENSE, $Notices, $InventoryJson, $InventoryText, docs/THIRD_PARTY_LICENSES.md -Destination $LicenseStage
$LicenseZip = Join-Path $Artifacts "InferBridge-$Version-third-party-licenses.zip"
Invoke-Checked "Create third-party license archive" {
    Compress-Archive -Path (Join-Path $LicenseStage "*") -DestinationPath $LicenseZip -CompressionLevel Optimal -Force
}
$Produced += $LicenseZip

$ReleaseNotesSource = Join-Path $Root "docs\releases\$Version.md"
if (-not (Test-Path $ReleaseNotesSource)) { throw "Structured release notes are required at docs/releases/$Version.md." }
$ReleaseNotes = Join-Path $Artifacts "InferBridge-$Version-release-notes.md"
Copy-Item $ReleaseNotesSource $ReleaseNotes -Force
$Produced += $ReleaseNotes

$ModelLibrarySource = Join-Path $Root "model_library_manifest.json"
if (-not (Test-Path $ModelLibrarySource)) { throw "Curated model library manifest is missing at $ModelLibrarySource." }
Invoke-Checked "Validate model library manifest" { & $ReleasePython scripts/validate_model_library_manifest.py $ModelLibrarySource }
$ModelLibraryAsset = Join-Path $Artifacts "model-library-manifest.json"
Copy-Item $ModelLibrarySource $ModelLibraryAsset -Force
$Produced += $ModelLibraryAsset

$TimingArtifact = Join-Path $Artifacts "InferBridge-$Version-release-timings.json"
Write-ReleaseTimingSnapshot -Path $TimingArtifact -Finalized $true
$Produced += $TimingArtifact

$PublishedAt = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")
Invoke-Checked "Generate and validate release manifest" {
    & $ReleasePython scripts/release_tools.py manifest --output-dir $Artifacts --version $Version --channel $Channel --published-at $PublishedAt --commit $GitCommit --clean ($TreeClean.ToString().ToLowerInvariant()) "--signed-types=$($SignedTypes -join ',')" --inventory-filename ([IO.Path]::GetFileName($InventoryJson))
}
$Manifest = Join-Path $Artifacts "InferBridge-$Version-release-manifest.json"
$Produced += $Manifest

$SummaryPath = Join-Path $Artifacts "InferBridge-$Version-release-summary.json"
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
    release_environment_key = $EnvironmentKey
    release_environment_reused = [bool]$script:ReleaseEnvironmentReused
    timing_telemetry = [IO.Path]::GetFileName($TimingArtifact)
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
Invoke-Checked "Generate SHA-256 checksums" { & $ReleasePython scripts/release_tools.py checksums --output-dir $Artifacts --version $Version }
$Checksums = Join-Path $Artifacts "InferBridge-$Version-checksums.txt"
$Produced += $Checksums

foreach ($Artifact in $Produced) {
    Invoke-ArtifactScan -Path $Artifact
}
Invoke-Checked "Verify final SHA-256 checksums" { & $ReleasePython scripts/release_tools.py verify-checksums --path $Checksums }
Write-ReleaseTimingSnapshot -Path $script:TimingSnapshotPath -Finalized $true

Write-Host "Release build completed:"
Get-ChildItem $Artifacts -File | Sort-Object Name | ForEach-Object { Write-Host "  $($_.FullName)" }
Write-Host "Detailed final timing log: $script:TimingSnapshotPath"
