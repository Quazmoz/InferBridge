<#
.SYNOPSIS
    Full setup flow for the InferBridge server.

.DESCRIPTION
    1. Runs network connectivity diagnostics.
    2. Runs hardware / Python preflight checks.
    3. Creates a Python virtual environment and installs runtime dependencies.
    4. Optionally installs the model-conversion dependencies.

.PARAMETER Minimal
    Skip installing requirements-convert.txt (optimum-intel, nncf).

.PARAMETER SkipHardwareCheck
    Skip the hardware/Python preflight.

.PARAMETER Python
    Python launcher to use (default: "py -3.11"; falls back through supported
    launchers, common direct install paths, then "python").
#>
[CmdletBinding()]
param(
    [switch]$Minimal,
    [switch]$SkipHardwareCheck,
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"
$SetupRoot = Split-Path -Parent $PSScriptRoot
$RepoRoot = Split-Path -Parent $SetupRoot

Write-Host "Repo: $RepoRoot" -ForegroundColor DarkGray

# --- Network Connectivity Preflight ---
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "  Network connectivity diagnostics" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

$pypiOk = $false
$hfOk = $false

try {
    Write-Host "Checking connection to PyPI (pypi.org)... " -NoNewline
    $null = Invoke-RestMethod -Uri "https://pypi.org/pypi/fastapi/json" -TimeoutSec 5 -ErrorAction Stop
    Write-Host "SUCCESS" -ForegroundColor Green
    $pypiOk = $true
} catch {
    Write-Host "FAILED" -ForegroundColor Red
    Write-Host "          Error details: $($_.Exception.Message)" -ForegroundColor DarkGray
}

try {
    Write-Host "Checking connection to Hugging Face (huggingface.co)... " -NoNewline
    $null = Invoke-RestMethod -Uri "https://huggingface.co/api/models/TinyLlama/TinyLlama-1.1B-Chat-v1.0" -TimeoutSec 5 -ErrorAction Stop
    Write-Host "SUCCESS" -ForegroundColor Green
    $hfOk = $true
} catch {
    Write-Host "FAILED" -ForegroundColor Red
    Write-Host "          Error details: $($_.Exception.Message)" -ForegroundColor DarkGray
}

if (-not $pypiOk -or -not $hfOk) {
    Write-Host ""
    Write-Host "WARNING: Network check failed for PyPI or Hugging Face." -ForegroundColor Yellow
    Write-Host "         This can prevent package downloads or model conversions." -ForegroundColor Yellow
    Write-Host "         Troubleshooting suggestions:" -ForegroundColor Yellow
    Write-Host "         - If behind a corporate proxy, configure HTTP_PROXY and HTTPS_PROXY environment variables." -ForegroundColor Yellow
    Write-Host "         - Disable VPN/firewalls temporarily or configure custom cert bundles." -ForegroundColor Yellow
    Write-Host ""
}

if (-not $SkipHardwareCheck) {
    & "$PSScriptRoot\check_hardware.ps1"
    Write-Host ""
}

$installArgs = @{ Python = $Python }
if (-not $Minimal) { $installArgs["WithConvert"] = $true }
& "$PSScriptRoot\install_deps.ps1" @installArgs

# --- Configure non-secret environment defaults if needed ---
$envFile = Join-Path $RepoRoot ".env"
$envExampleFile = Join-Path $RepoRoot ".env.example"
if (-not (Test-Path $envFile) -and (Test-Path $envExampleFile)) {
    Copy-Item -Path $envExampleFile -Destination $envFile
    Write-Host "Created .env from .env.example" -ForegroundColor Green
}

Write-Host ""
Write-Host "Hugging Face access:" -ForegroundColor Cyan
Write-Host "  Configure gated-model access from Settings after starting InferBridge." -ForegroundColor White
Write-Host "  Windows stores the token with DPAPI; setup never copies tokens into .env." -ForegroundColor White
Write-Host "  HF_TOKEN remains available only as an advanced environment fallback." -ForegroundColor DarkGray

Write-Host ""
Write-Host "==========================================" -ForegroundColor Green
Write-Host "  Setup complete." -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "  1. Start the server (runs the mock engine by default on first load):" -ForegroundColor Cyan
Write-Host "       .\start_server.bat" -ForegroundColor White
Write-Host "  2. Open the chat UI and prepare models dynamically:  http://localhost:8000" -ForegroundColor Cyan
Write-Host "  3. (Optional) Convert a model manually via terminal:" -ForegroundColor Cyan
Write-Host "       .\setup\windows\convert_model.ps1 -Id tinyllama-1.1b-chat-fp16" -ForegroundColor White
