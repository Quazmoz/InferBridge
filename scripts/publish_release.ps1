[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Version,
    [ValidateSet("stable", "beta", "nightly")][string]$Channel = "stable",
    [string]$ArtifactDirectory = "",
    [switch]$DryRun,
    [switch]$AllowUnsigned,
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $Root
if (-not $ArtifactDirectory) { $ArtifactDirectory = Join-Path $Root "artifacts\release-$Version" }
$ArtifactDirectory = (Resolve-Path $ArtifactDirectory).Path

& $Python scripts/release_tools.py validate-version --version $Version --channel $Channel
if ($LASTEXITCODE -ne 0) { throw "Invalid version or channel." }
& $Python scripts/release_tools.py verify-version-consistency --root $Root --version $Version
if ($LASTEXITCODE -ne 0) { throw "Version consistency check failed." }
if (-not [string]::IsNullOrWhiteSpace((git status --porcelain))) { throw "Publishing requires a clean working tree." }
$HeadCommit = (& git rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $HeadCommit -notmatch '^[0-9a-f]{40}$') { throw "Could not resolve the source commit." }

$Tag = "v$Version"
$Expected = @(
    "InferBridge-$Version-windows-x64-installer.exe",
    "InferBridge-$Version-windows-x64-portable.zip",
    "InferBridge-$Version-checksums.txt",
    "InferBridge-$Version-release-manifest.json",
    "InferBridge-$Version-third-party-licenses.zip",
    "InferBridge-$Version-release-notes.md",
    "InferBridge-$Version-dependency-inventory.json",
    "InferBridge-$Version-dependency-freeze.txt",
    "InferBridge-$Version-release-summary.json",
    "InferBridge-$Version-release-timings.json",
    "model-library-manifest.json"
)
$LibraryManifestSource = Join-Path $Root "model_library_manifest.json"
if (-not (Test-Path $LibraryManifestSource)) { throw "Missing model library manifest: $LibraryManifestSource" }
& $Python scripts/validate_model_library_manifest.py $LibraryManifestSource
if ($LASTEXITCODE -ne 0) { throw "Model library manifest validation failed." }
& $Python scripts/verify_release_provenance.py --artifact-directory $ArtifactDirectory --version $Version --channel $Channel --expected-commit $HeadCommit --source-model-manifest $LibraryManifestSource
if ($LASTEXITCODE -ne 0) { throw "Release provenance validation failed. Rebuild from the current clean commit." }
$SigningGate = @("scripts/verify_release_signing.py", "--artifact-directory", $ArtifactDirectory, "--version", $Version)
if ($Channel -eq "stable" -and -not $AllowUnsigned) { $SigningGate += "--require-signed" }
& $Python @SigningGate
if ($LASTEXITCODE -ne 0) {
    throw "Release signatures were not independently verified. Stable releases require signed installer and launcher artifacts."
}

& git rev-parse --verify --quiet "refs/tags/$Tag" | Out-Null
if ($LASTEXITCODE -eq 0) { throw "Tag $Tag already exists." }
$null = cmd /c "gh release view $Tag --repo Quazmoz/openvino-windows-llm >NUL 2>NUL"
if ($LASTEXITCODE -eq 0) { throw "GitHub release $Tag already exists." }

$Notes = Join-Path $ArtifactDirectory "InferBridge-$Version-release-notes.md"
$Upload = $Expected | ForEach-Object { Join-Path $ArtifactDirectory $_ }
if ($DryRun) {
    Write-Host "Dry run passed. Would create annotated tag $Tag and upload:"
    $Upload | ForEach-Object { Write-Host "  $_" }
    exit 0
}

git tag -a $Tag $HeadCommit -m "InferBridge $Version"
if ($LASTEXITCODE -ne 0) { throw "Annotated tag creation failed." }
if ((git rev-list -n 1 $Tag).Trim() -ne $HeadCommit) { throw "Created tag does not target the recorded source commit." }
git push origin $Tag
if ($LASTEXITCODE -ne 0) { throw "Tag push failed." }

$Arguments = @("release", "create", $Tag, "--repo", "Quazmoz/openvino-windows-llm", "--verify-tag", "--title", "InferBridge $Version", "--notes-file", $Notes)
if ($Channel -ne "stable") { $Arguments += "--prerelease" }
$Arguments += $Upload
& gh @Arguments
if ($LASTEXITCODE -ne 0) { throw "GitHub release creation or upload failed." }
$AssetJson = & gh release view $Tag --repo Quazmoz/openvino-windows-llm --json assets
if ($LASTEXITCODE -ne 0) { throw "Published release could not be re-read for artifact verification." }
$PublishedNames = @((($AssetJson | ConvertFrom-Json).assets) | ForEach-Object { $_.name })
foreach ($Name in $Expected) {
    if ($PublishedNames -notcontains $Name) { throw "Published release is missing artifact: $Name" }
}
Write-Host "Published and verified GitHub release $Tag."
