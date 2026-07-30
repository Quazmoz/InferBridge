[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$DistributionPath,
    [string]$Python = "python",
    [int]$HeadlessSeconds = 120,
    [ValidateSet("installed", "portable")][string]$ExpectedMode = "portable"
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$Root = (Resolve-Path $DistributionPath).Path
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Exe = Join-Path $Root "OpenVINOWindowsLLM.exe"
if (-not (Test-Path $Exe)) { throw "Executable not found: $Exe" }
$PortableMarker = Join-Path $Root "portable.flag"
$IsPortable = $ExpectedMode -eq "portable"
if ($IsPortable -and -not (Test-Path $PortableMarker)) {
    throw "Portable-mode smoke test requires portable.flag in the distribution root."
}
if (-not $IsPortable -and (Test-Path $PortableMarker)) {
    throw "Installed-mode smoke test refuses a distribution containing portable.flag."
}

$InternalRoot = (Join-Path $Root "_internal").TrimEnd('\')
if (-not (Test-Path $InternalRoot -PathType Container)) {
    throw "Packaged distribution is missing the PyInstaller _internal directory."
}
$PsutilWindowsBinaries = @(Get-ChildItem $Root -Recurse -File -Filter "_psutil_windows*.pyd")
if ($PsutilWindowsBinaries.Count -ne 1) {
    $Found = if ($PsutilWindowsBinaries.Count) {
        ($PsutilWindowsBinaries | ForEach-Object { $_.FullName.Substring($Root.Length).TrimStart('\') }) -join ", "
    } else {
        "none"
    }
    throw "Packaged distribution must contain exactly one psutil Windows extension; found $($PsutilWindowsBinaries.Count): $Found"
}
$InternalPrefix = $InternalRoot + [IO.Path]::DirectorySeparatorChar
$PsutilWindowsPath = [IO.Path]::GetFullPath($PsutilWindowsBinaries[0].FullName)
if (-not $PsutilWindowsPath.StartsWith($InternalPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "The psutil Windows extension must be contained under _internal."
}

$Data = Join-Path ([IO.Path]::GetTempPath()) ("OV LLM Packaged Smoke " + [guid]::NewGuid().ToString("N"))
New-Item $Data -ItemType Directory -Force | Out-Null
$Process = $null

try {
    $Arguments = @(
        "--mock", "--headless", "--headless-seconds", "$HeadlessSeconds",
        "--data-dir", ('"' + $Data + '"'), "--no-browser"
    )
    if ($IsPortable) { $Arguments += "--portable" }
    $Process = Start-Process -FilePath $Exe -ArgumentList $Arguments -WorkingDirectory $Root -PassThru -WindowStyle Hidden

    $MetadataPath = Join-Path $Data "desktop-instance.json"
    $Deadline = [DateTime]::UtcNow.AddSeconds(90)
    do {
        if ($Process.HasExited) { throw "Packaged tray exited with code $($Process.ExitCode)." }
        Start-Sleep -Milliseconds 300
    } until ((Test-Path $MetadataPath) -or [DateTime]::UtcNow -gt $Deadline)
    if (-not (Test-Path $MetadataPath)) { throw "Packaged tray did not publish server metadata." }

    $Metadata = Get-Content -Raw $MetadataPath | ConvertFrom-Json
    $Origin = "http://127.0.0.1:$($Metadata.port)"
    $Deadline = [DateTime]::UtcNow.AddSeconds(90)
    do {
        if ($Process.HasExited) { throw "Packaged tray exited with code $($Process.ExitCode)." }
        Start-Sleep -Milliseconds 300
        try { $Ready = Invoke-RestMethod "$Origin/health/ready" -TimeoutSec 3 } catch { $Ready = $null }
    } until (($Ready.status -eq "ready") -or [DateTime]::UtcNow -gt $Deadline)
    if ($Ready.status -ne "ready") { throw "Packaged server did not become ready." }

    $Instance = Invoke-RestMethod "$Origin/desktop/instance" -TimeoutSec 10
    if ($Instance.instance_nonce -ne $Metadata.nonce) { throw "Packaged desktop identity did not match tray metadata." }
    $Release = Invoke-RestMethod "$Origin/desktop/release/status" -TimeoutSec 10
    if (-not $Release.build.application_version) { throw "Packaged release metadata is missing." }
    if ($Release.installation_mode -ne $ExpectedMode) {
        throw "Packaged runtime reported installation mode '$($Release.installation_mode)' instead of '$ExpectedMode'."
    }
    $Ui = Invoke-WebRequest -UseBasicParsing -Uri "$Origin/" -TimeoutSec 20
    if ($Ui.Content -notmatch "About & Updates") { throw "Packaged About and Updates UI is missing." }

    $ValidatorArguments = @(
        (Join-Path $RepoRoot "scripts\validate_api_contract.py"),
        "--base-url", $Origin,
        "--profile", "full",
        "--model", "tinyllama-1.1b-chat-fp16",
        "--device", "CPU",
        "--expect-mock",
        "--include-embeddings",
        "--run-benchmark",
        "--exercise-lifecycle"
    )
    & $Python @ValidatorArguments
    if ($LASTEXITCODE -ne 0) { throw "Packaged external API contract validation failed." }

    @{ command = "quit"; created_at = [DateTime]::UtcNow.ToString("o") } |
        ConvertTo-Json | Set-Content -Path (Join-Path $Data "tray-command.json") -Encoding utf8
    if (-not $Process.WaitForExit(20000)) { throw "Packaged tray did not shut down after the owned quit command." }
    if ($Process.ExitCode -ne 0) { throw "Packaged tray exited with code $($Process.ExitCode)." }
    Write-Host "Packaged $ExpectedMode-mode tray-owned mock smoke test passed."
}
finally {
    if ($Process -and -not $Process.HasExited) {
        Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
        $Process.WaitForExit(10000) | Out-Null
    }
    Remove-Item $Data -Recurse -Force -ErrorAction SilentlyContinue
}
