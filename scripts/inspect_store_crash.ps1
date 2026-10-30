[CmdletBinding()]
param(
    [string]$EvtxPath = "",
    [datetime]$Since = (Get-Date).AddMinutes(-20),
    [string]$OutputPath = "",
    [ValidateRange(1, 100)][int]$MaxEvents = 40
)

# Read the Store's downloaded .evtx on a Windows machine, or collect relevant
# recent local events after a failed installed-MSIX smoke test. No upload occurs.
# Messages can contain local paths or personal information: review before sharing.
$ErrorActionPreference = "Stop"
$records = @()
$source = "live"

if ($EvtxPath) {
    $source = "evtx"
    $resolved = (Resolve-Path -LiteralPath $EvtxPath -ErrorAction Stop).Path
    if ([IO.Path]::GetExtension($resolved) -ine ".evtx") {
        throw "The certification crash-log input must be an .evtx file."
    }
    $records = @(Get-WinEvent -Path $resolved -MaxEvents ($MaxEvents * 20) -ErrorAction Stop)
} else {
    foreach ($logName in @(
        "Application",
        "Microsoft-Windows-AppModel-Runtime/Admin",
        "Microsoft-Windows-Immersive-Shell/Operational"
    )) {
        try {
            $recent = @(Get-WinEvent -FilterHashtable @{
                LogName = $logName
                StartTime = $Since
            } -MaxEvents ($MaxEvents * 5) -ErrorAction Stop)
            $records += $recent
        } catch [System.Exception] {
            # Some channels are disabled, missing or inaccessible. Keep results
            # from the other channels, without hiding a failed MSIX smoke test.
            Write-Warning "Could not read $($logName): $($_.Exception.GetType().Name)"
        }
    }
}

$matching = @($records | Where-Object {
    $message = try { [string]$_.Message } catch { "" }
    $properties = ($_.Properties | ForEach-Object { [string]$_.Value }) -join " "
    ($message + " " + $properties) -match "(?i)InferBridge|QuinnFavo[.]InferBridge"
})
# A reviewer-supplied log can contain only the activation error, with no
# application name. Preserve a bounded fallback instead of declaring it clean.
$chosen = if ($matching.Count -gt 0) { $matching } elseif ($EvtxPath) { $records } else { @() }
$items = @($chosen | Sort-Object TimeCreated -Descending | Select-Object -First $MaxEvents | ForEach-Object {
    $message = try { [string]$_.Message } catch { "" }
    if ([string]::IsNullOrWhiteSpace($message)) {
        $message = ($_.Properties | ForEach-Object { [string]$_.Value }) -join "; "
    }
    [pscustomobject]@{
        utc = if ($_.TimeCreated) { $_.TimeCreated.ToUniversalTime().ToString("o") } else { $null }
        log = $_.LogName
        provider = $_.ProviderName
        id = $_.Id
        level = $_.LevelDisplayName
        message = if ($message.Length -gt 6000) { $message.Substring(0, 6000) } else { $message }
    }
})
$result = [pscustomobject]@{
    source = $source
    matching_event_count = $matching.Count
    exported_event_count = $items.Count
    note = "Local diagnostic only; event messages may include paths or personal information. Review before sharing. Absence of events is not proof of a successful launch."
    events = $items
}
$json = $result | ConvertTo-Json -Depth 5
if ($OutputPath) {
    $parent = Split-Path -Parent $OutputPath
    if ($parent) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
    Set-Content -LiteralPath $OutputPath -Value $json -Encoding UTF8
    Write-Host "Crash triage saved to $OutputPath"
} else {
    Write-Output $json
}
