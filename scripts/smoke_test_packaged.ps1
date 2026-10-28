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
$Exe = Join-Path $Root "InferBridge.exe"
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
$OptimumRegisterRoot = Join-Path $InternalRoot "optimum\commands\register"
$OpenVinoRegistration = Join-Path $OptimumRegisterRoot "register_openvino.py"
if (-not (Test-Path $OpenVinoRegistration -PathType Leaf)) {
    throw "Packaged distribution is missing Optimum's OpenVINO CLI registration module."
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

# Diagnostics for a packaged tray that exits early or never becomes ready. The tray
# only logs its startup exception into the data directory, which this script deletes,
# so print bounded, redacted tails before cleanup. Secrets never leave the runner.
function Get-RedactedText([string]$Text) {
    if ([string]::IsNullOrEmpty($Text)) { return $Text }
    $Redacted = $Text
    $Redacted = [regex]::Replace($Redacted, '(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+', 'Bearer [redacted]')
    $Redacted = [regex]::Replace($Redacted, '(?i)("?(?:[a-z_-]*(?:token|nonce|secret|password|passwd|api[_-]?key|authorization|cookie|credential)[a-z_-]*)"?\s*[:=]\s*)("[^"]*"|[^\s,;}]+)', '$1"[redacted]"')
    $Redacted = [regex]::Replace($Redacted, '(?i)(--(?:instance-nonce|control-token)[ =])("[^"]*"|\S+)', '$1[redacted]')
    $Redacted = [regex]::Replace($Redacted, '\bhf_[A-Za-z0-9]{8,}', 'hf_[redacted]')
    $Redacted = [regex]::Replace($Redacted, '\bsk-[A-Za-z0-9_-]{8,}', 'sk-[redacted]')
    return $Redacted
}

function Write-PackagedStartupDiagnostics([string]$DataRoot, [string]$Reason, [string[]]$StdioFiles = @(), [int]$TailLines = 120) {
    Write-Host "::group::Packaged tray startup diagnostics"
    Write-Host "Smoke failure: $(Get-RedactedText $Reason)"
    try {
        foreach ($StdioFile in $StdioFiles) {
            if (-not $StdioFile -or -not (Test-Path -LiteralPath $StdioFile -PathType Leaf)) { continue }
            Write-Host "----- $(([IO.Path]::GetFileName($StdioFile) -split '\.', 2)[1]) (last $TailLines lines) -----"
            try {
                foreach ($Line in @(Get-Content -LiteralPath $StdioFile -Tail $TailLines -ErrorAction Stop)) {
                    Write-Host (Get-RedactedText ([string]$Line))
                }
            }
            catch {
                Write-Host "(unreadable: $($_.Exception.Message))"
            }
        }
        if (-not $DataRoot -or -not (Test-Path -LiteralPath $DataRoot)) {
            Write-Host "Data directory was not created: $DataRoot"
            return
        }
        $Candidates = @()
        foreach ($Name in @("desktop-instance.json", "tray-heartbeat.json", "tray-command.json")) {
            $Path = Join-Path $DataRoot $Name
            if (Test-Path -LiteralPath $Path -PathType Leaf) { $Candidates += Get-Item -LiteralPath $Path }
        }
        $LogsRoot = Join-Path $DataRoot "logs"
        if (Test-Path -LiteralPath $LogsRoot -PathType Container) {
            $Candidates += @(Get-ChildItem -LiteralPath $LogsRoot -File -Recurse -ErrorAction SilentlyContinue |
                Where-Object { $_.Extension -in @(".log", ".txt", ".json") -or $_.Name -match '\.log\.\d+$' } |
                Sort-Object FullName)
        }
        if (-not $Candidates.Count) {
            Write-Host "No tray logs or state files were written under $DataRoot."
        }
        foreach ($Item in $Candidates) {
            $Relative = $Item.FullName.Substring($DataRoot.Length).TrimStart('\', '/')
            Write-Host "----- $Relative (last $TailLines lines) -----"
            try {
                $Lines = @(Get-Content -LiteralPath $Item.FullName -Tail $TailLines -ErrorAction Stop)
                foreach ($Line in $Lines) { Write-Host (Get-RedactedText ([string]$Line)) }
            }
            catch {
                Write-Host "(unreadable: $($_.Exception.Message))"
            }
        }
    }
    finally {
        Write-Host "::endgroup::"
    }
}

$NativeSmoke = Start-Process -FilePath $Exe -ArgumentList "--native-smoke" -WorkingDirectory $Root -PassThru -Wait -WindowStyle Hidden
if ($NativeSmoke.ExitCode -ne 0) {
    throw "Packaged OpenVINO native runtime smoke test failed with exit code $($NativeSmoke.ExitCode)."
}

$Data = Join-Path ([IO.Path]::GetTempPath()) ("OV LLM Packaged Smoke " + [guid]::NewGuid().ToString("N"))
New-Item $Data -ItemType Directory -Force | Out-Null
# Tray stdio is captured outside the data directory so the application owns its data
# root exactly as in a user launch; the headless startup failure reason lands on stderr.
$TrayStdout = "$Data.tray-stdout.log"
$TrayStderr = "$Data.tray-stderr.log"
$Process = $null
$PreviousPortEnvironment = [Environment]::GetEnvironmentVariable("OV_LLM_PORT", "Process")

try {
    # Exercise the packaged launcher setting, not merely the server's actual port.
    # Bind port zero temporarily to ask Windows for an unused, nondefault test port.
    # A pinned OV_LLM_PORT must be free on BOTH the wildcard and loopback addresses,
    # exactly as app.desktop_controller.choose_available_port probes it, so select
    # only a port that passes the same check. The launcher must still fail closed if
    # another process acquires it afterward.
    $RequestedPort = 0
    for ($Attempt = 0; $Attempt -lt 8 -and $RequestedPort -eq 0; $Attempt++) {
        $Probe = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
        try {
            $Probe.Start()
            $Candidate = ([System.Net.IPEndPoint]$Probe.LocalEndpoint).Port
        }
        finally {
            $Probe.Stop()
        }
        if ($Candidate -eq 8000) { continue }
        $LauncherCompatible = $true
        foreach ($Address in @([System.Net.IPAddress]::Any, [System.Net.IPAddress]::Loopback)) {
            $Socket = [System.Net.Sockets.Socket]::new(
                [System.Net.Sockets.AddressFamily]::InterNetwork,
                [System.Net.Sockets.SocketType]::Stream,
                [System.Net.Sockets.ProtocolType]::Tcp)
            try {
                $Socket.Bind([System.Net.IPEndPoint]::new($Address, $Candidate))
            }
            catch {
                $LauncherCompatible = $false
            }
            finally {
                $Socket.Dispose()
            }
            if (-not $LauncherCompatible) { break }
        }
        if ($LauncherCompatible) { $RequestedPort = $Candidate }
    }
    if ($RequestedPort -eq 0 -or $RequestedPort -eq 8000) {
        throw "Could not select a nondefault port for the packaged OV_LLM_PORT smoke test."
    }
    $env:OV_LLM_PORT = [string]$RequestedPort

    $Arguments = @(
        "--mock", "--headless", "--headless-seconds", "$HeadlessSeconds",
        "--data-dir", ('"' + $Data + '"'), "--no-browser"
    )
    if ($IsPortable) { $Arguments += "--portable" }
    $Process = Start-Process -FilePath $Exe -ArgumentList $Arguments -WorkingDirectory $Root -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput $TrayStdout -RedirectStandardError $TrayStderr

    $MetadataPath = Join-Path $Data "desktop-instance.json"
    $Deadline = [DateTime]::UtcNow.AddSeconds(90)
    do {
        if ($Process.HasExited) { throw "Packaged tray exited with code $($Process.ExitCode)." }
        Start-Sleep -Milliseconds 300
    } until ((Test-Path $MetadataPath) -or [DateTime]::UtcNow -gt $Deadline)
    if (-not (Test-Path $MetadataPath)) { throw "Packaged tray did not publish server metadata." }

    $Metadata = Get-Content -Raw $MetadataPath | ConvertFrom-Json
    if ($Metadata.port -ne $RequestedPort) {
        throw "Packaged desktop ignored OV_LLM_PORT: configured $RequestedPort; actual $($Metadata.port)."
    }
    $Origin = "http://127.0.0.1:$($Metadata.port)"
    $Deadline = [DateTime]::UtcNow.AddSeconds(90)
    do {
        if ($Process.HasExited) { throw "Packaged tray exited with code $($Process.ExitCode)." }
        Start-Sleep -Milliseconds 300
        try { $Ready = Invoke-RestMethod "$Origin/health/ready" -TimeoutSec 3 } catch { $Ready = $null }
    } until (($Ready.status -eq "ready") -or [DateTime]::UtcNow -gt $Deadline)
    if ($Ready.status -ne "ready") { throw "Packaged server did not become ready." }

    $Instance = Invoke-RestMethod "$Origin/desktop/instance" -TimeoutSec 10
    if ($Instance.instance_nonce -ne $Metadata.nonce) {
        $NoncePresent = -not [string]::IsNullOrWhiteSpace([string]$Instance.instance_nonce)
        $NonceLengthMatches = ([string]$Instance.instance_nonce).Length -eq ([string]$Metadata.nonce).Length
        $PortMatches = $Instance.port -eq $Metadata.port
        throw "Packaged desktop identity did not match tray metadata (nonce present: $NoncePresent; nonce lengths match: $NonceLengthMatches; ports match: $PortMatches)."
    }
    $Release = Invoke-RestMethod "$Origin/desktop/release/status" -TimeoutSec 10
    if (-not $Release.build.application_version) { throw "Packaged release metadata is missing." }
    if ($Release.installation_mode -ne $ExpectedMode) {
        throw "Packaged runtime reported installation mode '$($Release.installation_mode)' instead of '$ExpectedMode'."
    }
    $Ui = Invoke-WebRequest -UseBasicParsing -Uri "$Origin/" -TimeoutSec 20
    # Feature payloads are served as content-addressed /ui/ assets rather than inlined
    # into the document, so the document carries the script reference and the asset
    # carries the markup. Follow the reference so this still proves the surface ships.
    $ReleaseScript = [regex]::Match(
        $Ui.Content, '<script id="ovllm-release-extension" src="([^"]+)"')
    if (-not $ReleaseScript.Success) {
        throw "Packaged UI document does not reference the About and Updates extension."
    }
    $ReleaseAsset = Invoke-WebRequest -UseBasicParsing -Uri "$Origin$($ReleaseScript.Groups[1].Value)" -TimeoutSec 20
    if ($ReleaseAsset.StatusCode -ne 200) {
        throw "Packaged About and Updates asset did not load: HTTP $($ReleaseAsset.StatusCode)."
    }
    if ($ReleaseAsset.Content -notmatch "About & Updates") {
        throw "Packaged About and Updates UI is missing."
    }

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
catch {
    $FailureReason = $_.Exception.Message
    if ($Process -and -not $Process.HasExited) {
        # Stop the tray first so its log handles are flushed and readable.
        Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
        $Process.WaitForExit(10000) | Out-Null
    }
    Write-PackagedStartupDiagnostics -DataRoot $Data -Reason $FailureReason -StdioFiles @($TrayStderr, $TrayStdout)
    throw
}
finally {
    if ($null -eq $PreviousPortEnvironment) {
        Remove-Item Env:OV_LLM_PORT -ErrorAction SilentlyContinue
    }
    else {
        $env:OV_LLM_PORT = $PreviousPortEnvironment
    }
    if ($Process -and -not $Process.HasExited) {
        Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
        $Process.WaitForExit(10000) | Out-Null
    }
    Remove-Item $Data -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $TrayStdout, $TrayStderr -Force -ErrorAction SilentlyContinue
}
