[CmdletBinding()]
param(
    [string]$Python = "python",
    [string]$OutputDirectory = "artifacts\native-dev",
    [string]$IsccPath = $env:ISCC_PATH
)

# Local, unsigned evaluation installer. Reuse the pinned engine build and its
# smoke tests; no release publication, signing, tags or branch promotion.
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $Root
$OutputDirectory = if ([IO.Path]::IsPathRooted($OutputDirectory)) {
    [IO.Path]::GetFullPath($OutputDirectory)
} else {
    [IO.Path]::GetFullPath((Join-Path $Root $OutputDirectory))
}
if (-not $IsccPath) {
    $IsccPath = @(
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
        (Get-Command ISCC.exe -ErrorAction SilentlyContinue).Source
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
}
if (-not $IsccPath -or -not (Test-Path -LiteralPath $IsccPath)) {
    throw "Inno Setup 6 compiler was not found. Set ISCC_PATH or pass -IsccPath."
}

& dotnet run --project windows-native/InferBridge.Native.ContractTests/InferBridge.Native.ContractTests.csproj -c Release
if ($LASTEXITCODE -ne 0) { throw "Native backend contract tests failed." }

$Dist = Join-Path $OutputDirectory "payload"
& (Join-Path $PSScriptRoot "build_dev_package.ps1") -Python $Python -OutputDirectory $Dist
if ($LASTEXITCODE -ne 0) { throw "Engine build or packaged smoke test failed." }
$BuiltRoot = Join-Path $Dist "InferBridge"
$NativeRoot = Join-Path $BuiltRoot "native"
& dotnet publish windows-native/InferBridge.Native/InferBridge.Native.csproj -c Release `
    -r win-x64 --self-contained true -p:Platform=x64 -o $NativeRoot
if ($LASTEXITCODE -ne 0) { throw "Native client publish failed. Install the .NET 10 SDK." }

$Version = (& $Python scripts/release_tools.py canonical-version).Trim()
if ($LASTEXITCODE -ne 0 -or -not $Version) { throw "Could not read application version." }
$NumericVersion = (($Version -split '[-+]')[0]) + ".0"
$AppIcon = Join-Path $Root "build\dev-package\brand\InferBridge.ico"
& $Python scripts/release_tools.py scan --path $BuiltRoot
if ($LASTEXITCODE -ne 0) { throw "Combined payload safety scan failed." }
& $IsccPath /DNativeFrontend /DNativeEvaluation "/DMyAppVersion=$Version" "/DMyAppVersionNumeric=$NumericVersion" `
    "/DSourceRoot=$BuiltRoot" "/DArtifactDir=$OutputDirectory" "/DAppIconPath=$AppIcon" packaging/installer.iss
if ($LASTEXITCODE -ne 0) { throw "Native installer compilation failed." }
$Installer = Join-Path $OutputDirectory "InferBridge-$Version-native-windows-x64-installer.exe"
if (-not (Test-Path -LiteralPath $Installer)) { throw "Native installer was not produced." }
$Hash = (Get-FileHash -LiteralPath $Installer -Algorithm SHA256).Hash.ToLowerInvariant()
"$Hash  $([IO.Path]::GetFileName($Installer))" | Set-Content -LiteralPath "$Installer.sha256" -Encoding ascii
Write-Host "Unsigned native evaluation installer: $Installer"
