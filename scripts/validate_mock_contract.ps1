[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Python,
    [string]$OutputDirectory = "build\release\mock-contract",
    [int]$Port = 8766
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Output = if ([IO.Path]::IsPathRooted($OutputDirectory)) {
    $OutputDirectory
} else {
    Join-Path $Root $OutputDirectory
}
New-Item $Output -ItemType Directory -Force | Out-Null

$Stdout = Join-Path $Output "server.stdout.log"
$Stderr = Join-Path $Output "server.stderr.log"
$JsonReport = Join-Path $Output "api-contract.json"
$MarkdownReport = Join-Path $Output "api-contract.md"
$PreviousMock = $env:OV_LLM_MOCK
$PreviousKey = $env:OV_LLM_API_KEY
$Process = $null

try {
    $env:OV_LLM_MOCK = "1"
    $env:OV_LLM_API_KEY = ""
    $Process = Start-Process -FilePath $Python -ArgumentList @(
        "-m", "app.server", "--host", "127.0.0.1", "--port", "$Port",
        "--model", "tinyllama-1.1b-chat-fp16", "--device", "CPU"
    ) -WorkingDirectory $Root -RedirectStandardOutput $Stdout -RedirectStandardError $Stderr -PassThru -WindowStyle Hidden

    $Origin = "http://127.0.0.1:$Port"
    $Deadline = [DateTime]::UtcNow.AddSeconds(60)
    do {
        if ($Process.HasExited) { throw "Mock contract server exited with code $($Process.ExitCode)." }
        Start-Sleep -Milliseconds 300
        try { $Health = Invoke-RestMethod "$Origin/health" -TimeoutSec 3 } catch { $Health = $null }
    } until (($Health.status -in @("ok", "busy")) -or [DateTime]::UtcNow -gt $Deadline)
    if ($Health.status -notin @("ok", "busy")) { throw "Timed out waiting for the mock contract server." }

    $ValidatorArguments = @(
        "scripts/validate_api_contract.py",
        "--base-url", $Origin,
        "--profile", "full",
        "--model", "tinyllama-1.1b-chat-fp16",
        "--device", "CPU",
        "--expect-mock",
        "--include-embeddings",
        "--include-rerank",
        "--exercise-slots",
        "--run-benchmark",
        "--exercise-lifecycle",
        "--output-json", $JsonReport,
        "--output-markdown", $MarkdownReport
    )
    & $Python @ValidatorArguments
    if ($LASTEXITCODE -ne 0) { throw "External mock API contract validation failed." }
}
finally {
    if ($Process -and -not $Process.HasExited) {
        Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
        $Process.WaitForExit(10000) | Out-Null
    }
    $env:OV_LLM_MOCK = $PreviousMock
    $env:OV_LLM_API_KEY = $PreviousKey
}

Write-Host "External mock API contract validation passed: $MarkdownReport"
