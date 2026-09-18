[CmdletBinding()]
param(
    [string]$Model = "tinyllama-1.1b-chat-fp16",
    [string[]]$Devices = @("CPU", "GPU", "NPU", "AUTO"),
    [string]$EmbeddingModel = "bge-small-en-v1.5",
    [switch]$IncludeEmbeddings,
    [switch]$SkipConversion,
    [switch]$ContinueOnFailure,
    [switch]$KeepServerLogs,
    [string]$ApiKey = $env:OV_LLM_API_KEY,
    [string]$OutputDirectory = "certification/results",
    [int]$Port = 8765,
    [int]$LoadTimeoutSeconds = 900,
    [int]$RequestTimeoutSeconds = 120,
    [int]$ContextDepth = 0
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

function Test-Windows {
    if (Get-Variable IsWindows -ErrorAction SilentlyContinue) { return [bool]$IsWindows }
    return $env:OS -eq "Windows_NT"
}

function Get-Python([string]$Root) {
    $venv = Join-Path $Root ".venv\Scripts\python.exe"
    if (Test-Path $venv) { return $venv }
    $command = Get-Command python -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    throw "Python was not found. Run .\setup.bat first."
}

function Stop-Server([AllowNull()][System.Diagnostics.Process]$Process) {
    if ($Process -and -not $Process.HasExited) {
        Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
        $Process.WaitForExit(10000) | Out-Null
        Start-Sleep -Milliseconds 250
    }
}

function Wait-PortFree([int]$LocalPort, [int]$TimeoutSeconds = 30) {
    # After a prior device's server is force-stopped the listener can linger briefly
    # while the OS releases the socket. Poll instead of failing on the first check so a
    # device transition never aborts the remaining certification targets.
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        $listener = Get-NetTCPConnection -LocalPort $LocalPort -State Listen -ErrorAction SilentlyContinue
        if (-not $listener) { return }
        Start-Sleep -Milliseconds 500
    }
    throw "Port $LocalPort is still in use after $TimeoutSeconds seconds."
}

function Wait-Health(
    [string]$BaseUrl,
    [System.Diagnostics.Process]$Process,
    [int]$TimeoutSeconds
) {
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        if ($Process.HasExited) { throw "Server exited with code $($Process.ExitCode)." }
        try {
            $health = Invoke-RestMethod "$BaseUrl/health" -TimeoutSec 5
            if ($health.status -in @("ok", "busy")) { return }
        }
        catch { Start-Sleep 1 }
    }
    throw "Timed out waiting for $BaseUrl/health."
}

function Safe-Text([string]$Text, [string]$Secret) {
    if (-not $Text) { return "" }
    if ($Secret) { $Text = $Text.Replace($Secret, "[redacted]") }
    $Text = [regex]::Replace($Text, "(?i)Bearer\s+\S+|hf_[A-Za-z0-9_=-]{8,}", "[redacted]")
    return [regex]::Replace($Text, "[A-Za-z]:\\[^`r`n]+", "[local-path]")
}

function Get-CacheSnapshot([string]$Path) {
    $files = @(
        if (Test-Path -LiteralPath $Path) {
            Get-ChildItem -LiteralPath $Path -Recurse -File -ErrorAction SilentlyContinue
        }
    )
    $rows = @($files | ForEach-Object {
        "$($_.FullName.Substring($Path.Length).TrimStart('\'))|$($_.Length)"
    } | Sort-Object)
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [Text.Encoding]::UTF8.GetBytes(($rows -join "`n"))
        $fingerprint = ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace("-", "").ToLowerInvariant()
    }
    finally {
        $sha.Dispose()
    }
    return [ordered]@{
        file_count = $files.Count
        bytes = [long](($files | Measure-Object Length -Sum).Sum)
        fingerprint = $fingerprint
    }
}

function Python-Json([string]$Python, [string]$Code) {
    # Execute the snippet from a temporary file instead of `python -c "<code>"`.
    # Windows PowerShell 5.1 strips embedded double quotes when forwarding a
    # multi-line -c argument to a native process, which corrupts JSON dictionary
    # keys (e.g. {"openvino": ...} becomes {openvino: ...}) and makes discovery
    # fail with a NameError. A script file is passed through verbatim on every
    # PowerShell edition. stderr is captured to a file (not merged with 2>&1) so
    # a benign warning cannot trip $ErrorActionPreference = "Stop".
    $scriptFile = Join-Path ([IO.Path]::GetTempPath()) ("ovllm-discovery-" + [Guid]::NewGuid().ToString("N") + ".py")
    $errFile = "$scriptFile.err"
    Set-Content -LiteralPath $scriptFile -Value $Code -Encoding UTF8
    try {
        $output = & $Python $scriptFile 2>$errFile
        if ($LASTEXITCODE -ne 0) {
            $details = Get-Content -LiteralPath $errFile -Raw -ErrorAction SilentlyContinue
            throw "Python helper exited with code $LASTEXITCODE. $details"
        }
        return ($output -join [Environment]::NewLine) | ConvertFrom-Json
    }
    finally {
        Remove-Item -LiteralPath $scriptFile, $errFile -Force -ErrorAction SilentlyContinue
    }
}

if (-not (Test-Windows)) { throw "This harness must run on Windows." }

$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Push-Location $root
$server = $null
$oldKey = $env:OV_LLM_API_KEY
$oldMock = $env:OV_LLM_MOCK
$oldAuto = $env:OV_LLM_AUTO_CONVERT
$oldCache = $env:OV_LLM_CACHE_DIR

try {
    $python = Get-Python $root
    $validator = Join-Path $root "scripts\validate_api_contract.py"
    $contextValidator = Join-Path $root "scripts\certify_context_depth.py"
    if (-not (Test-Path $validator)) { throw "Missing $validator" }
    if (-not (Test-Path $contextValidator)) { throw "Missing $contextValidator" }

    $outputRoot = if ([IO.Path]::IsPathRooted($OutputDirectory)) {
        $OutputDirectory
    } else {
        Join-Path $root $OutputDirectory
    }
    $stamp = [DateTime]::UtcNow.ToString("yyyyMMdd-HHmmss")
    $session = Join-Path $outputRoot "windows-certification-$stamp"
    New-Item -ItemType Directory -Path $session -Force | Out-Null

    $deviceCode = @'
import json
from runtime import device_check
available = device_check.available_devices()
print(json.dumps({
    "openvino": device_check.is_openvino_available(),
    "available": available,
    "details": device_check.device_details(),
}))
'@
    $discovery = Python-Json $python $deviceCode
    if (-not $discovery.openvino) { throw "OpenVINO is not available in this environment." }
    $available = @($discovery.available)
    if ($available.Count -eq 0) { throw "OpenVINO reported no devices." }

    $packageCode = @'
import importlib.metadata as metadata
import json
names = ["openvino", "openvino-genai", "optimum-intel", "nncf", "fastapi", "pydantic"]
versions = {}
for name in names:
    try:
        versions[name] = metadata.version(name)
    except metadata.PackageNotFoundError:
        versions[name] = None
print(json.dumps(versions))
'@
    $packages = Python-Json $python $packageCode
    $os = Get-CimInstance Win32_OperatingSystem
    $computer = Get-CimInstance Win32_ComputerSystem
    $machine = [ordered]@{
        os = "$($os.Caption) $($os.Version) build $($os.BuildNumber)"
        architecture = $os.OSArchitecture
        memory_gb = [Math]::Round($computer.TotalPhysicalMemory / 1GB, 2)
        processors = @(Get-CimInstance Win32_Processor | Select-Object -ExpandProperty Name)
        video_controllers = @(Get-CimInstance Win32_VideoController | ForEach-Object {
            [ordered]@{ name = $_.Name; driver_version = $_.DriverVersion }
        })
        python = (& $python --version 2>&1) -join " "
        packages = $packages
    }

    # The documented `powershell.exe -File .\scripts\validate_windows.ps1 -Devices
    # CPU,GPU,NPU,AUTO` form passes the whole comma list as one literal string, unlike an
    # in-session `.\validate_windows.ps1 -Devices CPU,GPU,NPU,AUTO` call which PowerShell
    # splits into an array. Normalise both so every requested device is certified
    # individually instead of once as a bogus "CPU,GPU,NPU,AUTO" target.
    $Devices = @($Devices | ForEach-Object { $_ -split "[,;]" } | ForEach-Object { $_.Trim() } | Where-Object { $_ })

    $results = @()
    $embeddingPending = [bool]$IncludeEmbeddings
    foreach ($rawDevice in $Devices) {
        $device = $rawDevice.Trim().ToUpperInvariant()
        if (-not $device) { continue }
        if ($device -in @("CPU", "GPU", "NPU") -and $device -notin $available) {
            $results += [ordered]@{
                device = $device
                status = "skipped"
                reason = "OpenVINO did not report this device."
                summary = [ordered]@{ pass = 0; warn = 0; skip = 1; fail = 0 }
            }
            continue
        }

        $slug = ($device -replace "[^A-Z0-9]+", "-").Trim("-").ToLowerInvariant()
        $stdout = Join-Path $session "server-$slug.stdout.log"
        $stderr = Join-Path $session "server-$slug.stderr.log"
        $jsonReport = Join-Path $session "api-$slug.json"
        $mdReport = Join-Path $session "api-$slug.md"
        $contextReport = Join-Path $session "context-$slug.json"
        $compiledCache = Join-Path $session "compiled-cache-$slug"
        $baseUrl = "http://127.0.0.1:$Port"

        $env:OV_LLM_API_KEY = $ApiKey
        $env:OV_LLM_MOCK = ""
        $env:OV_LLM_AUTO_CONVERT = if ($SkipConversion) { "" } else { "1" }
        $env:OV_LLM_CACHE_DIR = $compiledCache
        $serverArgs = @(
            "-m", "app.server", "--host", "127.0.0.1", "--port", "$Port",
            "--model", $Model, "--device", $device
        )
        if (-not $SkipConversion) { $serverArgs += "--auto-convert" }

        Write-Host "Validating $Model on $device..."
        $stopAfterDevice = $false
        try {
            # Wait for the previous device's server to release the port, then start
            # inside the try so a transition failure is recorded per device and honors
            # -ContinueOnFailure instead of aborting the whole certification run.
            Wait-PortFree $Port
            $start = @{
                FilePath = $python
                ArgumentList = $serverArgs
                WorkingDirectory = $root
                RedirectStandardOutput = $stdout
                RedirectStandardError = $stderr
                PassThru = $true
                WindowStyle = "Hidden"
            }
            $server = Start-Process @start
            Wait-Health $baseUrl $server 60
            $validatorArgs = @(
                $validator, "--base-url", $baseUrl, "--profile", "full",
                "--model", $Model, "--device", $device, "--expect-real",
                "--run-benchmark", "--exercise-lifecycle",
                "--timeout", "$RequestTimeoutSeconds",
                "--load-timeout", "$LoadTimeoutSeconds",
                "--output-json", $jsonReport, "--output-markdown", $mdReport
            )
            if ($embeddingPending) {
                $validatorArgs += @("--include-embeddings", "--embedding-model", $EmbeddingModel)
            }
            & $python @validatorArgs
            $exitCode = $LASTEXITCODE
            $api = Get-Content -Raw $jsonReport | ConvertFrom-Json
            $context = $null
            $cacheReuse = $null
            $contextExitCode = 1
            if ($exitCode -eq 0 -and $api.summary.fail -eq 0) {
                Invoke-RestMethod "$baseUrl/v1/models/unload" -Method Post -ContentType "application/json" -Body (@{ model = $Model } | ConvertTo-Json) -Headers $(if ($ApiKey) { @{ Authorization = "Bearer $ApiKey" } } else { @{} }) | Out-Null
                $cacheAfterFirstProcess = Get-CacheSnapshot $compiledCache
                & $python $contextValidator --model $Model --device $device --context $ContextDepth --output $contextReport
                $contextExitCode = $LASTEXITCODE
                if (Test-Path $contextReport) {
                    $context = Get-Content -Raw $contextReport | ConvertFrom-Json
                    $context.error = Safe-Text ([string]$context.error) $ApiKey
                    $context | ConvertTo-Json -Depth 6 | Set-Content $contextReport -Encoding UTF8
                }
                $cacheAfterFirstRestart = Get-CacheSnapshot $compiledCache
                if ($contextExitCode -eq 0) {
                    & $python $contextValidator --model $Model --device $device --context $ContextDepth --output $contextReport
                    $contextExitCode = $LASTEXITCODE
                }
                $cacheAfterSecondRestart = Get-CacheSnapshot $compiledCache
                $cacheReuse = [ordered]@{
                    server_process = $cacheAfterFirstProcess
                    first_restart = $cacheAfterFirstRestart
                    second_restart = $cacheAfterSecondRestart
                    reused = [bool](
                        $cacheAfterFirstRestart.file_count -gt 0 -and
                        $cacheAfterFirstRestart.fingerprint -eq $cacheAfterSecondRestart.fingerprint -and
                        $contextExitCode -eq 0
                    )
                }
            }
            $status = if ($exitCode -eq 0 -and $api.summary.fail -eq 0 -and $contextExitCode -eq 0 -and $context.passed -and $cacheReuse.reused) { "passed" } else { "failed" }
            $results += [ordered]@{
                device = $device
                status = $status
                reason = ""
                summary = $api.summary
                report = Split-Path -Leaf $mdReport
                context_report = if ($context) { Split-Path -Leaf $contextReport } else { $null }
                context_depth = $context
                compiled_cache_reuse = $cacheReuse
            }
            $embeddingPending = $false
            if ($status -eq "failed" -and -not $ContinueOnFailure) {
                $stopAfterDevice = $true
            }
        }
        catch {
            if (-not @($results | Where-Object device -eq $device).Count) {
                $results += [ordered]@{
                    device = $device
                    status = "failed"
                    reason = Safe-Text $_.Exception.Message $ApiKey
                    summary = [ordered]@{ pass = 0; warn = 0; skip = 0; fail = 1 }
                }
            }
            if (-not $ContinueOnFailure) { $stopAfterDevice = $true }
            Write-Warning $_.Exception.Message
        }
        finally {
            Stop-Server $server
            $server = $null
            if (-not $KeepServerLogs) {
                Remove-Item $stdout, $stderr -Force -ErrorAction SilentlyContinue
            }
            if (Test-Path -LiteralPath $compiledCache) {
                Remove-Item -LiteralPath $compiledCache -Recurse -Force -ErrorAction SilentlyContinue
            }
        }
        if ($stopAfterDevice) { break }
    }

    $summary = [ordered]@{
        passed = @($results | Where-Object status -eq "passed").Count
        skipped = @($results | Where-Object status -eq "skipped").Count
        failed = @($results | Where-Object status -eq "failed").Count
    }
    if ($results.Count -ne $Devices.Count) {
        throw "Certification produced $($results.Count) device results for $($Devices.Count) requested devices."
    }
    $report = [ordered]@{
        schema_version = 2
        generated_at = [DateTime]::UtcNow.ToString("o")
        model = $Model
        embedding_model = if ($IncludeEmbeddings) { $EmbeddingModel } else { $null }
        requested_devices = @($Devices)
        detected_devices = $available
        device_details = $discovery.details
        machine = $machine
        summary = $summary
        device_results = $results
    }
    $jsonPath = Join-Path $session "windows-certification.json"
    $mdPath = Join-Path $session "windows-certification.md"
    $report | ConvertTo-Json -Depth 12 | Set-Content $jsonPath -Encoding UTF8

    $lines = @(
        "# OpenVINO Windows Hardware Certification", "",
        "- Generated: ``$($report.generated_at)``",
        "- Model: ``$Model``",
        "- Detected devices: ``$($available -join ', ')``", "",
        "**Result:** $($summary.passed) passed, $($summary.skipped) skipped, $($summary.failed) failed.", "",
        "| Device | Status | API passed | Warnings | Context requested | Prompt tokens | Tokens generated | Beyond rejected | Cache reused | Actual device |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|"
    )
    foreach ($result in $results) {
        $context = if ($result.Contains("context_depth")) { $result.context_depth } else { $null }
        $contextRequested = if ($context) { $context.requested_context } else { "-" }
        $promptTokens = if ($context) { $context.prompt_tokens } else { "-" }
        $tokensGenerated = if ($context) { $context.tokens_generated } else { "-" }
        $beyondRejected = if ($context) { $context.beyond_rejected } else { "-" }
        $actualDevice = if ($context) { $context.actual_device } else { "-" }
        $cacheReused = if ($result.Contains("compiled_cache_reuse") -and $result.compiled_cache_reuse) { $result.compiled_cache_reuse.reused } else { "-" }
        $lines += "| $($result.device) | **$($result.status.ToUpperInvariant())** | $($result.summary.pass) | $($result.summary.warn) | $contextRequested | $promptTokens | $tokensGenerated | $beyondRejected | $cacheReused | $actualDevice |"
    }
    $lines += @(
        "", "> Reports exclude API keys, prompts, generated text, hostnames, usernames, serial numbers, and full local paths.", ""
    )
    Set-Content $mdPath ($lines -join [Environment]::NewLine) -Encoding UTF8
    Write-Host "Certification report: $mdPath"
    if ($summary.failed -gt 0) { exit 1 }
}
finally {
    Stop-Server $server
    $env:OV_LLM_API_KEY = $oldKey
    $env:OV_LLM_MOCK = $oldMock
    $env:OV_LLM_AUTO_CONVERT = $oldAuto
    $env:OV_LLM_CACHE_DIR = $oldCache
    Pop-Location
}
