<#
.SYNOPSIS
    Windows / Python / Intel device preflight for OpenVINO.

.DESCRIPTION
    Reports OS, Python, CPU, GPU, and (best-effort) NPU presence. This is
    informational: OpenVINO can run on CPU almost anywhere, so the script warns
    rather than failing. GPU/NPU acceleration depends on Intel drivers.
#>
[CmdletBinding()]
param([switch]$AllowUnsupported)

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "  Hardware / environment preflight" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

# --- OS ---
$osCaption = "Unknown Windows Version"
$osBuild = "Unknown"
try {
    $os = Get-CimInstance Win32_OperatingSystem -ErrorAction Stop
    $osCaption = $os.Caption
    $osBuild = $os.BuildNumber
} catch {
    try {
        $os = Get-WmiObject Win32_OperatingSystem -ErrorAction Stop
        $osCaption = $os.Caption
        $osBuild = $os.BuildNumber
    } catch {}
}
Write-Host ("OS:       {0} (build {1})" -f $osCaption, $osBuild)

# --- Python ---
$SupportedPythonVersions = @("3.11", "3.12", "3.13")
$pythonOk = $false
$unsupportedPythonVersions = [Collections.Generic.List[string]]::new()
# Match the versions accepted by install_deps.ps1 and pyproject.toml.
$candidates = @("py -3.11", "py -3.12", "py -3.13", "py", "python")

function Get-PythonVersion {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,
        [string[]]$Arguments = @()
    )

    try {
        $output = & $FilePath @Arguments -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
        if ($LASTEXITCODE -ne 0) { return $null }
        $line = $output | Select-Object -Last 1
        if ($null -eq $line) { return $null }
        return $line.ToString().Trim()
    } catch {
        return $null
    }
}

foreach ($candidate in $candidates) {
    $parts = $candidate.Split(" ", 2)
    $exe = $parts[0]
    $arguments = if ($parts.Length -gt 1) { @($parts[1]) } else { @() }

    # Skip Microsoft Store dummy python aliases because they hang non-interactive sessions.
    if ($exe -match "^python") {
        $resolved = Get-Command $exe -ErrorAction SilentlyContinue
        if ($resolved -and $resolved.Source -match "Microsoft\\WindowsApps") {
            continue
        }
    }

    $version = Get-PythonVersion -FilePath $exe -Arguments $arguments
    if (-not $version) { continue }
    if ($version -in $SupportedPythonVersions) {
        Write-Host ("Python:   Python {0}  ({1})" -f $version, $candidate) -ForegroundColor Green
        $pythonOk = $true
        break
    }
    if (-not $unsupportedPythonVersions.Contains($version)) {
        $unsupportedPythonVersions.Add($version)
    }
}
if (-not $pythonOk) {
    if ($unsupportedPythonVersions.Count -gt 0) {
        Write-Host ("Python:   UNSUPPORTED version detected: {0}. Install Python 3.11, 3.12, or 3.13." -f ($unsupportedPythonVersions -join ", ")) -ForegroundColor Red
    } else {
        Write-Host "Python:   NOT FOUND. Install Python 3.11, 3.12, or 3.13 from python.org." -ForegroundColor Red
    }
}

# --- Registry Long Paths Enabled Check & Auto-Remediation ---
$lpPath = "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem"
$lpName = "LongPathsEnabled"
$lpVal = $null
try {
    $lpVal = (Get-ItemProperty -Path $lpPath -Name $lpName -ErrorAction SilentlyContinue).$lpName
} catch {}

if ($lpVal -ne 1) {
    Write-Host "Windows:  Long Paths are NOT enabled. This may cause Hugging Face download or conversion errors." -ForegroundColor Yellow
    try {
        Write-Host "          Attempting registry auto-remediation to enable Long Paths..." -ForegroundColor Cyan
        Set-ItemProperty -Path $lpPath -Name $lpName -Value 1 -ErrorAction Stop
        Write-Host "          SUCCESS: Long Paths enabled in Registry!" -ForegroundColor Green
    } catch {
        Write-Host "          Auto-remediation failed (requires administrative privileges)." -ForegroundColor DarkGray
        Write-Host "          To fix manually, run this command in Administrator PowerShell:" -ForegroundColor DarkGray
        Write-Host "              Set-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem' -Name 'LongPathsEnabled' -Value 1" -ForegroundColor Gray
    }
} else {
    Write-Host "Windows:  Long Paths are enabled." -ForegroundColor Green
}

# --- Visual C++ Redistributable Check & Auto-Remediation ---
$vcRedistInstalled = $false
$vcKeys = @(
    "HKLM:\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64",
    "HKLM:\SOFTWARE\Wow6432Node\Microsoft\VisualStudio\14.0\VC\Runtimes\x64"
)
foreach ($key in $vcKeys) {
    if (Test-Path $key) {
        $installedVal = Get-ItemProperty -Path $key -Name "Installed" -ErrorAction SilentlyContinue
        if ($installedVal -and $installedVal.Installed -eq 1) {
            $vcRedistInstalled = $true
            break
        }
    }
}
if (-not $vcRedistInstalled) {
    $installerKeys = Get-ChildItem "HKLM:\SOFTWARE\Classes\Installer\Dependencies" -ErrorAction SilentlyContinue
    if ($installerKeys) {
        foreach ($k in $installerKeys) {
            if ($k.Name -match "VC,redist\.x64") {
                $vcRedistInstalled = $true
                break
            }
        }
    }
}

if ($vcRedistInstalled) {
    Write-Host "MSVC VC++: Redistributable (x64) is installed." -ForegroundColor Green
} else {
    Write-Host "MSVC VC++: Redistributable (x64) is NOT detected (required by OpenVINO runtime)." -ForegroundColor Yellow
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Host "          Attempting silent installation via winget..." -ForegroundColor Cyan
        try {
            Start-Process winget -ArgumentList "install --id Microsoft.VCRedist.2015+.x64 --silent --accept-source-agreements --accept-package-agreements" -NoNewWindow -Wait -ErrorAction Stop
            Write-Host "          SUCCESS: VC++ Redistributable installed!" -ForegroundColor Green
            $vcRedistInstalled = $true
        } catch {
            Write-Host "          winget installation failed or timed out." -ForegroundColor DarkGray
        }
    }
    if (-not $vcRedistInstalled) {
        Write-Host "          Please download and install it manually from Microsoft:" -ForegroundColor Red
        Write-Host "              https://aka.ms/vs/17/release/vc_redist.x64.exe" -ForegroundColor White
    }
}

# --- CPU ---
$cpu = "Unknown"
try {
    $cpu = (Get-CimInstance Win32_Processor | Select-Object -First 1 -ErrorAction Stop).Name
} catch {
    try {
        $cpu = (Get-WmiObject Win32_Processor | Select-Object -First 1 -ErrorAction Stop).Name
    } catch {}
}
Write-Host ("CPU:      {0}" -f $cpu)
if ($cpu -match "Ultra") {
    Write-Host "          Intel Core Ultra detected - NPU acceleration may be available." -ForegroundColor Green
}

# --- GPU ---
$gpus = @()
try {
    $gpus = Get-CimInstance Win32_VideoController -ErrorAction Stop | Select-Object -ExpandProperty Name
} catch {
    try {
        $gpus = Get-WmiObject Win32_VideoController -ErrorAction Stop | Select-Object -ExpandProperty Name
    } catch {}
}
foreach ($g in $gpus) {
    $color = if ($g -match "Intel") { "Green" } else { "DarkGray" }
    Write-Host ("GPU:      {0}" -f $g) -ForegroundColor $color
}

# --- NPU (best effort) ---
$npu = $null
try {
    $npu = Get-PnpDevice -ErrorAction Stop | Where-Object {
        $_.FriendlyName -match "\bNPU\b|AI Boost|Neural"
    }
} catch {
    Write-Host "NPU:      Get-PnpDevice is not supported or failed on this Windows version." -ForegroundColor DarkGray
}

if ($npu) {
    foreach ($n in $npu) {
        Write-Host ("NPU:      {0} [{1}]" -f $n.FriendlyName, $n.Status) -ForegroundColor Green

        # Try to query the signed driver version.
        try {
            $driver = $null
            try {
                $driver = Get-CimInstance -ClassName Win32_PnPSignedDriver -ErrorAction Stop | Where-Object {
                    $_.DeviceID -eq $n.DeviceId
                }
            } catch {
                $driver = Get-WmiObject -Class Win32_PnPSignedDriver -ErrorAction Stop | Where-Object {
                    $_.DeviceID -eq $n.DeviceId
                }
            }
            if ($driver) {
                $ver = $driver.DriverVersion
                $date = $driver.DriverDate
                Write-Host ("          Driver version: {0} ({1})" -f $ver, $date) -ForegroundColor Green

                # Check for minimum recommended driver version (32.0.100.3104).
                if ($ver -match "^(\d+)\.(\d+)\.(\d+)\.(\d+)") {
                    $major = [int]$Matches[1]
                    $minor = [int]$Matches[2]
                    $build1 = [int]$Matches[3]
                    $build2 = [int]$Matches[4]

                    $isOutdated = $false
                    if ($major -lt 32) { $isOutdated = $true }
                    elseif ($major -eq 32) {
                        if ($minor -lt 0) { $isOutdated = $true }
                        elseif ($minor -eq 0) {
                            if ($build1 -lt 100) { $isOutdated = $true }
                            elseif ($build1 -eq 100 -and $build2 -lt 3104) { $isOutdated = $true }
                        }
                    }

                    if ($isOutdated) {
                        Write-Host "          WARNING: NPU driver version is older than recommended baseline (32.0.100.3104)." -ForegroundColor Yellow
                        Write-Host "                   This may cause OpenVINO GenAI graph compilation failures or segfaults." -ForegroundColor Yellow
                        Write-Host "                   Please update your drivers from the Intel Download Center." -ForegroundColor Yellow
                    }
                }
            }
        } catch {
            Write-Host "          Could not retrieve NPU driver version." -ForegroundColor DarkGray
        }
    }
} else {
    Write-Host "NPU:      not detected via PnP (CPU/GPU will still work)." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Preflight is informational; CPU inference works without GPU/NPU drivers." -ForegroundColor DarkGray
exit 0
