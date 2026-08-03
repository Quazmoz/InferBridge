<#
.SYNOPSIS
    Create the project venv and install Python dependencies.

.PARAMETER WithConvert
    Also install requirements-convert.txt (optimum-intel, nncf).

.PARAMETER Python
    Python launcher (default: "py -3.11", falling back through newer supported
    launchers, common direct install paths, then "python").
#>
[CmdletBinding()]
param(
    [switch]$WithConvert,
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"
$SetupRoot = Split-Path -Parent $PSScriptRoot
$RepoRoot = Split-Path -Parent $SetupRoot
$VenvDir = Join-Path $RepoRoot ".venv"
$RequirementsPath = Join-Path $RepoRoot "requirements.txt"
$DependencyMarker = Join-Path $RepoRoot ".deps_installed"
$ProjectFile = Join-Path $RepoRoot "pyproject.toml"
$ProjectMarker = Join-Path $RepoRoot ".source_package_installed"
$ConversionRequirementsPath = Join-Path $RepoRoot "requirements-convert.txt"
$ConversionDependencyMarker = Join-Path $RepoRoot ".convert_deps_installed"
$SupportedPythonVersions = @("3.11", "3.12", "3.13")

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,
        [string[]]$Arguments = @()
    )

    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        $cmd = @($FilePath) + $Arguments
        throw "Command failed with exit code $LASTEXITCODE`: $($cmd -join ' ')"
    }
}

function Get-PythonVersion {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,
        [string[]]$Arguments = @()
    )

    try {
        $output = & $FilePath @Arguments -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
        if ($LASTEXITCODE -ne 0) { return $null }
        return (($output | Select-Object -Last 1).ToString().Trim())
    } catch {
        return $null
    }
}

function Resolve-Python {
    param([string]$Preferred)
    $candidates = @()
    if ($Preferred) { $candidates += $Preferred }
    $candidates += @(
        "py -3.11",
        "py -3.12",
        "py -3.13",
        "$env:LOCALAPPDATA\Python\pythoncore-3.11-64\python.exe",
        "$env:LOCALAPPDATA\Python\pythoncore-3.12-64\python.exe",
        "$env:LOCALAPPDATA\Python\pythoncore-3.13-64\python.exe"
    )
    $installRoots = @(
        "$env:LOCALAPPDATA\Python",
        "C:\Program Files"
    )
    foreach ($root in $installRoots) {
        $candidates += Get-ChildItem -Path $root -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -match "^(Python|pythoncore)-3\.(11|12|13)" } |
            Sort-Object FullName |
            ForEach-Object { Join-Path $_.FullName "python.exe" }
    }
    $candidates += "python"

    foreach ($c in $candidates) {
        if (-not $c) { continue }
        if (Test-Path $c) {
            $exe = $c
            $rest = @()
        } else {
            $parts = $c.Split(" ", 2)
            $exe = $parts[0]
            $rest = if ($parts.Length -gt 1) { $parts[1..($parts.Length - 1)] } else { @() }
        }
        $version = Get-PythonVersion -FilePath $exe -Arguments $rest
        if ($version -in $SupportedPythonVersions) {
            return ,@($exe, $rest)
        }
    }
    throw "No suitable Python found. Install Python 3.11, 3.12, or 3.13 from python.org, or pass -Python with the full python.exe path."
}


$py = Resolve-Python -Preferred $Python
$pyExe = $py[0]
$pyRest = $py[1]
$CreatedVenv = $false

if (-not (Test-Path $VenvDir)) {
    Write-Host "Creating virtual environment at $VenvDir ..." -ForegroundColor Cyan
    try {
        Invoke-Checked -FilePath $pyExe -Arguments ($pyRest + @("-m", "venv", $VenvDir))
        $CreatedVenv = $true
    } catch {
        Write-Host "ERROR: Failed to create virtual environment." -ForegroundColor Red
        Write-Host "       Check if you have write access to $RepoRoot or run setup in an Administrator terminal." -ForegroundColor Yellow
        throw $_
    }
} else {
    Write-Host "Using existing virtual environment at $VenvDir" -ForegroundColor DarkGray
}

$venvPython = Join-Path $VenvDir "Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    throw "The existing .venv is incomplete. Remove $VenvDir and run setup again."
}
$venvVersion = Get-PythonVersion -FilePath $venvPython
if ($venvVersion -notin $SupportedPythonVersions) {
    throw "The existing .venv uses unsupported Python $venvVersion. Remove $VenvDir and rerun setup with Python 3.11, 3.12, or 3.13."
}

try {
    Write-Host "Upgrading pip ..." -ForegroundColor Cyan
    Invoke-Checked -FilePath $venvPython -Arguments @("-m", "pip", "install", "--upgrade", "pip")

    Write-Host "Installing runtime dependencies (requirements.txt) ..." -ForegroundColor Cyan
    Invoke-Checked -FilePath $venvPython -Arguments @("-m", "pip", "install", "-r", $RequirementsPath)

    if ($WithConvert) {
        Write-Host "Installing conversion dependencies (requirements-convert.txt) ..." -ForegroundColor Cyan
        Invoke-Checked -FilePath $venvPython -Arguments @(
            "-m", "pip", "install", "-r", $ConversionRequirementsPath
        )
    }

    Write-Host "Registering the InferBridge source package ..." -ForegroundColor Cyan
    Invoke-Checked -FilePath $venvPython -Arguments @(
        "-m", "pip", "install", "--no-deps", "--editable", $RepoRoot
    )
} catch {
    $errText = $_.Exception.Message
    Write-Host ""
    Write-Host "ERROR: Dependency installation failed." -ForegroundColor Red
    Write-Host "Error details: $errText" -ForegroundColor Red
    Write-Host ""
    Write-Host "--------------------------------------------------" -ForegroundColor Yellow
    Write-Host "             TROUBLESHOOTING DIAGNOSTICS          " -ForegroundColor Yellow
    Write-Host "--------------------------------------------------" -ForegroundColor Yellow

    if ($errText -match "SSL" -or $errText -match "certificate verify failed" -or $errText -match "TLS") {
        Write-Host "[ISSUE] PIP SSL/TLS verification failed (common behind corporate proxies)." -ForegroundColor Yellow
        Write-Host "[FIX]   Try running pip install with trusted hosts manually:" -ForegroundColor Green
        Write-Host "        .\.venv\Scripts\python.exe -m pip install --trusted-host pypi.org --trusted-host files.pythonhosted.org -r requirements.txt" -ForegroundColor White
        Write-Host "        Or set the HTTP_PROXY / HTTPS_PROXY environment variables." -ForegroundColor Green
    }
    elseif ($errText -match "PermissionError" -or $errText -match "Access is denied" -or $errText -match "Permission denied") {
        Write-Host "[ISSUE] Access denied. Pip could not write package files." -ForegroundColor Yellow
        Write-Host "[FIX]   Run setup in an Administrator command prompt or PowerShell session." -ForegroundColor Green
    }
    elseif ($errText -match "No space left on device" -or $errText -match "disk space" -or $errText -match "out of space") {
        Write-Host "[ISSUE] Out of disk space." -ForegroundColor Yellow
        Write-Host "[FIX]   Free up space on your installation drive and run setup again." -ForegroundColor Green
    }
    else {
        Write-Host "Generic pip installation failure. Remediation tips:" -ForegroundColor Yellow
        Write-Host " - Run installation manually avoiding pip cache:" -ForegroundColor Gray
        Write-Host "     .\.venv\Scripts\python.exe -m pip install -r requirements.txt --no-cache-dir" -ForegroundColor White
        Write-Host " - Ensure no other Python or server processes are locking venv files." -ForegroundColor Gray
    }
    Write-Host "--------------------------------------------------" -ForegroundColor Yellow
    throw $_
}

# Record the exact requirements content installed into this environment. Runtime and
# conversion profiles are tracked independently so Minimal setups stay lightweight.
$requirementsHash = (Get-FileHash -LiteralPath $RequirementsPath -Algorithm SHA256).Hash
Set-Content -LiteralPath $DependencyMarker -Value $requirementsHash -NoNewline -Encoding ascii
$projectHash = (Get-FileHash -LiteralPath $ProjectFile -Algorithm SHA256).Hash
Set-Content -LiteralPath $ProjectMarker -Value $projectHash -NoNewline -Encoding ascii

if ($WithConvert) {
    $conversionRequirementsHash = (
        Get-FileHash -LiteralPath $ConversionRequirementsPath -Algorithm SHA256
    ).Hash
    Set-Content -LiteralPath $ConversionDependencyMarker `
        -Value $conversionRequirementsHash -NoNewline -Encoding ascii
} elseif ($CreatedVenv) {
    # A newly created Minimal environment must not inherit a stale marker from a
    # previously deleted full environment in the same checkout.
    Remove-Item -LiteralPath $ConversionDependencyMarker -Force -ErrorAction SilentlyContinue
}

Write-Host "Dependencies installed." -ForegroundColor Green
Invoke-Checked -FilePath $venvPython -Arguments @("-m", "app.server", "--check-devices")
