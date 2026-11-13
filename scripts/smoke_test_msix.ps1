[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Package,
    [string]$EvidenceDirectory = "",
    [int]$TimeoutSeconds = 300,
    [switch]$RunCertificationKit,
    [switch]$RequireFreshProfile,
    [switch]$WindowOnly
)

# Installed-package smoke test for the Store MSIX: signs a copy with a throwaway
# self-signed certificate, installs it, launches it through its package identity
# (as the Start menu does) and verifies the native window, the bundled engine
# handoff and single-window relaunch. -RunCertificationKit then runs the Windows App
# Certification Kit on the same signed copy. Requires an elevated Windows PowerShell 5.1
# (the Appx cmdlets) and the Windows SDK. Close any running InferBridge first.
# It never deletes user data; %USERPROFILE%\InferBridgeStoreData is only read.
# -RequireFreshProfile fails closed before installation if the profile already
# contains Store data, rather than reporting a reused profile as first-run evidence.
# -WindowOnly stops once the packaged native window has stayed open: the launch gate
# for a structural package whose engine is a placeholder (no engine, relaunch or WACK).

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$TestStartedAt = (Get-Date).AddSeconds(-10)
$Package = (Resolve-Path $Package).Path
$StoreDataRoot = Join-Path $env:USERPROFILE "InferBridgeStoreData"
if ($RequireFreshProfile -and (Test-Path -LiteralPath $StoreDataRoot)) {
    throw "A fresh-profile Store test requires no existing InferBridgeStoreData. Use a new Windows test account; this test never deletes existing user data."
}
Add-Type -AssemblyName System.IO.Compression.FileSystem
$Archive = [IO.Compression.ZipFile]::OpenRead($Package)
try {
    $Reader = New-Object IO.StreamReader($Archive.GetEntry("AppxManifest.xml").Open())
    [xml]$Manifest = $Reader.ReadToEnd()
    $Reader.Dispose()
} finally {
    $Archive.Dispose()
}
$IdentityName = $Manifest.Package.Identity.Name
$Publisher = $Manifest.Package.Identity.Publisher
$Entrypoint = $Manifest.Package.Applications.Application.Executable
# The smoke test uninstalls its test package during cleanup. Never silently
# upgrade and then remove a user's pre-existing Store installation.
if (@(Get-AppxPackage -Name $IdentityName -ErrorAction Stop).Count -gt 0) {
    throw "InferBridge is already installed for this user. Use a dedicated Windows test account; the smoke test will not replace an existing package."
}
Write-Host "Package $IdentityName, entrypoint $Entrypoint"

# Keep evidence for review, but delete the temporary signed package and test
# certificate afterward. A multi-GB MSIX must not be left in the temp folder.
$Work = Join-Path ([IO.Path]::GetTempPath()) ("InferBridge-msix-smoke-" + [guid]::NewGuid().ToString("N"))
if (-not $EvidenceDirectory) {
    $EvidenceDirectory = Join-Path (Get-Location).Path ("artifacts\msix-smoke-" + [guid]::NewGuid().ToString("N"))
}
New-Item $EvidenceDirectory -ItemType Directory -Force | Out-Null
$EvidenceDirectory = (Resolve-Path $EvidenceDirectory).Path  # appcert.exe needs absolute paths
New-Item $Work -ItemType Directory -Force | Out-Null

# Activate through the package identity, exactly like a Start-menu launch, and
# get the started process ID back without depending on Explorer.
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
[ComImport, Guid("2e941141-7f97-4756-ba1d-9decde894a3d"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IApplicationActivationManager {
    [PreserveSig] int ActivateApplication([MarshalAs(UnmanagedType.LPWStr)] string appUserModelId,
        [MarshalAs(UnmanagedType.LPWStr)] string arguments, int options, out uint processId);
}
[ComImport, Guid("45BA127D-10A8-46EA-8AB7-56EA9078943C")]
public class ApplicationActivationManager { }
public static class PackageLauncher {
    public static uint Launch(string aumid) {
        uint processId;
        int result = ((IApplicationActivationManager)new ApplicationActivationManager())
            .ActivateApplication(aumid, null, 0, out processId);
        if (result < 0) Marshal.ThrowExceptionForHR(result);
        return processId;
    }
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
}
"@

function Wait-Until([scriptblock]$Condition, [string]$Description) {
    $Deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $Deadline) {
        $Result = & $Condition
        if ($Result) { return $Result }
        Start-Sleep -Seconds 2
    }
    throw "Timed out after $TimeoutSeconds s waiting for: $Description"
}

function Save-Screenshot([string]$Name) {
    try {
        Add-Type -AssemblyName System.Windows.Forms, System.Drawing
        $Bounds = [Windows.Forms.Screen]::PrimaryScreen.Bounds
        $Bitmap = New-Object Drawing.Bitmap $Bounds.Width, $Bounds.Height
        $Graphics = [Drawing.Graphics]::FromImage($Bitmap)
        $Graphics.CopyFromScreen($Bounds.Location, [Drawing.Point]::Empty, $Bounds.Size)
        $Bitmap.Save((Join-Path $EvidenceDirectory $Name), [Drawing.Imaging.ImageFormat]::Png)
        $Graphics.Dispose(); $Bitmap.Dispose()
    } catch {
        Write-Warning "Screenshot unavailable: $_"
    }
}

function Get-PackageProcesses([string]$Name, [string]$Root) {
    # Compare whole directory paths, not an unbounded prefix that could match
    # a neighboring package whose installation folder begins with the same text.
    $Prefix = $Root.TrimEnd([char[]]@('\', '/')) + '\'
    @(Get-Process -Name $Name -ErrorAction SilentlyContinue | Where-Object {
        $_.Path -and $_.Path.StartsWith($Prefix, [StringComparison]::OrdinalIgnoreCase)
    })
}

function Test-PackagedSession($Session, [string]$InstallRoot) {
    # A responding localhost API is not proof that this MSIX started its engine.
    # Bind the session nonce to a newly started server process inside this package.
    if (-not $Session -or [string]::IsNullOrWhiteSpace([string]$Session.nonce)) { return $false }
    $ServerPid = 0
    if (-not [int]::TryParse([string]$Session.pid, [ref]$ServerPid) -or $ServerPid -le 0) {
        return $false
    }
    $StartedAt = [datetimeoffset]::MinValue
    if (-not [datetimeoffset]::TryParse([string]$Session.started_at, [ref]$StartedAt)) {
        return $false
    }
    if ($StartedAt.UtcDateTime -lt $TestStartedAt.ToUniversalTime()) { return $false }
    $Server = Get-Process -Id $ServerPid -ErrorAction SilentlyContinue
    if (-not $Server) { return $false }
    try {
        # Metadata alone can report an old start timestamp inaccurately. Check
        # the OS-owned process creation time as well as its executable path.
        if ($Server.StartTime.ToUniversalTime() -lt $TestStartedAt.ToUniversalTime()) {
            return $false
        }
        return [string]::Equals(
            $Server.Path, (Join-Path $InstallRoot "InferBridge.exe"),
            [StringComparison]::OrdinalIgnoreCase
        )
    } catch { return $false }
}

$Certificate = $null
$Trusted = $null
$Installed = $null
try {
    $SignTool = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\signtool.exe" -ErrorAction SilentlyContinue |
        Sort-Object { [version]$_.Directory.Parent.Name } -Descending | Select-Object -First 1
    if (-not $SignTool) { throw "signtool.exe was not found. Install the Windows 10/11 SDK." }

    # Sideloading requires a trusted signature whose subject equals the manifest publisher.
    $Certificate = New-SelfSignedCertificate -Type Custom -Subject $Publisher -KeyUsage DigitalSignature `
        -FriendlyName "InferBridge MSIX smoke test (temporary)" -CertStoreLocation Cert:\CurrentUser\My `
        -TextExtension @("2.5.29.37={text}1.3.6.1.5.5.7.3.3", "2.5.29.19={text}")
    $Signed = Join-Path $Work "InferBridge-smoke-signed.msix"
    Copy-Item $Package $Signed
    & $SignTool.FullName sign /fd SHA256 /sha1 $Certificate.Thumbprint /s My $Signed | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "signtool failed to sign the smoke-test copy." }
    $CerFile = Join-Path $Work "smoke.cer"
    Export-Certificate -Cert $Certificate -FilePath $CerFile | Out-Null
    $Trusted = Import-Certificate -FilePath $CerFile -CertStoreLocation Cert:\LocalMachine\TrustedPeople

    Add-AppxPackage -Path $Signed
    $Installed = Get-AppxPackage -Name $IdentityName
    if (-not $Installed) { throw "The package did not register after installation." }
    $InstallRoot = $Installed.InstallLocation.TrimEnd('\') + '\'
    $Aumid = "$($Installed.PackageFamilyName)!InferBridge"
    Write-Host "Installed $($Installed.PackageFullName) at $InstallRoot"

    $LaunchedId = [PackageLauncher]::Launch($Aumid)
    $Launched = Get-Process -Id $LaunchedId -ErrorAction SilentlyContinue
    $ExpectedPath = Join-Path $InstallRoot $Entrypoint
    if (-not $Launched -or $Launched.Path -ne $ExpectedPath) {
        throw "Package activation started '$($Launched.Path)' instead of $ExpectedPath."
    }

    # A real top-level window must appear (a crashed or headless client has none).
    $Window = Wait-Until {
        Get-PackageProcesses "InferBridge.Native" $InstallRoot | Where-Object {
            $_.Refresh(); $_.MainWindowHandle -ne 0
        } | Select-Object -First 1
    } "the native InferBridge window"
    Write-Host "Native window visible: '$($Window.MainWindowTitle)' (PID $($Window.Id))"
    if ($Window.MainWindowTitle -ne "InferBridge") { throw "Unexpected window title '$($Window.MainWindowTitle)'." }

    if ($WindowOnly) {
        # A launch crash can show a window for a moment; it must still be there.
        Start-Sleep -Seconds 15
        $Window.Refresh()
        if ($Window.HasExited) { throw "The native window closed within 15 s of launch." }
        Save-Screenshot "native-window-only.png"
        Write-Host "MSIX window-only smoke test passed: the packaged native window stays open."
        return
    }

    # The window starts the bundled engine; it must publish verified session metadata.
    $Metadata = Join-Path $env:USERPROFILE "InferBridgeStoreData\desktop-instance.json"
    $Session = Wait-Until {
        if (-not (Test-Path $Metadata)) { return $null }
        try {
            $Current = Get-Content $Metadata -Raw | ConvertFrom-Json
            if (-not (Test-PackagedSession $Current $InstallRoot)) { return $null }
            $Identity = Invoke-RestMethod "http://127.0.0.1:$($Current.port)/desktop/instance" -TimeoutSec 3
            $Health = Invoke-RestMethod "http://127.0.0.1:$($Current.port)/health/live" -TimeoutSec 3
            if ($Identity.instance_nonce -eq $Current.nonce -and $Health.status -eq "ok") { return $Current }
        } catch { }
        return $null
    } "the bundled engine to report a verified, live session"
    Write-Host "Engine verified on 127.0.0.1:$($Session.port)"
    if (-not (Test-PackagedSession $Session $InstallRoot)) {
        throw "The verified session is not owned by this freshly launched packaged InferBridge.exe."
    }

    # Give the window time to poll and show the connected state before capturing it.
    Start-Sleep -Seconds 12
    [void][PackageLauncher]::SetForegroundWindow($Window.MainWindowHandle)
    Start-Sleep -Seconds 2
    Save-Screenshot "native-window-connected.png"

    # Drive the UI through UI Automation (as Narrator would): the Model library
    # must list the packaged engine's catalog, which is the first-run path.
    Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
    $Ui = [Windows.Automation.AutomationElement]::FromHandle($Window.MainWindowHandle)
    function Find-ByName([string]$Name) {
        $Condition = New-Object Windows.Automation.PropertyCondition(
            [Windows.Automation.AutomationElement]::NameProperty, $Name)
        $Ui.FindFirst([Windows.Automation.TreeScope]::Descendants, $Condition)
    }
    $LibraryTab = Find-ByName "Model library"
    if (-not $LibraryTab) { throw "The Model library navigation item is not exposed to UI Automation." }
    try {
        $LibraryTab.GetCurrentPattern([Windows.Automation.SelectionItemPattern]::Pattern).Select()
    } catch [InvalidOperationException] {
        $LibraryTab.GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern).Invoke()
    }
    $Rows = Wait-Until {
        $List = Find-ByName "Engine model inventory"
        if (-not $List) { return $null }
        $Items = $List.FindAll([Windows.Automation.TreeScope]::Children, [Windows.Automation.Condition]::TrueCondition)
        if ($Items.Count -gt 0) { return ,$Items }
        return $null
    } "the native Model library to list the engine catalog"
    Write-Host "Model library lists $($Rows.Count) models; first: '$($Rows[0].Current.Name)'"
    try {
        $Advice = Wait-Until {
            $First = (Find-ByName "Engine model inventory").FindFirst(
                [Windows.Automation.TreeScope]::Children, [Windows.Automation.Condition]::TrueCondition)
            if ($First -and $First.Current.Name.StartsWith("Recommended")) { return $First.Current.Name }
            return $null
        } "the engine recommendation"
        Write-Host "Recommendation shown: '$Advice'"
    } catch {
        Write-Warning "No recommendation shown (advice only): $_"
    }
    Save-Screenshot "native-model-library.png"

    # A second launch must focus the existing window, not open another.
    [void][PackageLauncher]::Launch($Aumid)
    Start-Sleep -Seconds 10
    $Windows = @(Get-PackageProcesses "InferBridge.Native" $InstallRoot | Where-Object { $_.HasExited -eq $false })
    if ($Windows.Count -ne 1) { throw "Relaunch left $($Windows.Count) native client processes; expected 1." }
    if ($Windows[0].Id -ne $Window.Id) { throw "Relaunch replaced the original native window." }
    $Owners = Get-Content $Metadata -Raw | ConvertFrom-Json
    if (-not (Test-PackagedSession $Owners $InstallRoot) -or $Owners.pid -ne $Session.pid -or
        $Owners.nonce -ne $Session.nonce) {
        throw "Relaunch replaced or lost the authoritative packaged engine session."
    }
    Save-Screenshot "native-window-after-relaunch.png"
    Write-Host "MSIX smoke test passed: native window, engine handoff and single-instance relaunch."

    if ($RunCertificationKit) {
        $AppCert = "${env:ProgramFiles(x86)}\Windows Kits\10\App Certification Kit\appcert.exe"
        if (-not (Test-Path $AppCert)) { throw "Windows App Certification Kit was not found at $AppCert." }
        # WACK installs and launches the package itself.
        Get-PackageProcesses "InferBridge.Native" $InstallRoot | Stop-Process -Force -ErrorAction SilentlyContinue
        Get-PackageProcesses "InferBridge" $InstallRoot | Stop-Process -Force -ErrorAction SilentlyContinue
        Remove-AppxPackage -Package $Installed.PackageFullName
        # Keep $Installed so finally can remove the test copy again if WACK
        # reinstalls it. It must never leave an installed test package behind.
        $Report = Join-Path $EvidenceDirectory "wack-report.xml"
        & $AppCert reset | Out-Null
        & $AppCert test -appxpackagepath $Signed -reportoutputpath $Report | Out-Host
        if (-not (Test-Path $Report)) { throw "The certification kit produced no report." }
        [xml]$Results = Get-Content $Report -Raw
        $Failed = @($Results.SelectNodes("//TEST[RESULT='FAIL']") | ForEach-Object { $_.NAME })
        Write-Host "WACK overall result: $($Results.REPORT.OVERALL_RESULT)"
        if ($Failed.Count) { Write-Host ("Failed WACK tests: " + ($Failed -join "; ")) }
        if ($Results.REPORT.OVERALL_RESULT -ne "PASS") { throw "Windows App Certification Kit did not pass." }
    }
} catch {
    Save-Screenshot "failure.png"
    # A native/engine launch crash can precede the UI and its own logging.
    # Preserve bounded Windows error and activation events before uninstall.
    try {
        & (Join-Path $PSScriptRoot "inspect_store_crash.ps1") -Since $TestStartedAt -OutputPath (Join-Path $EvidenceDirectory "store-activation-events.json") | Out-Host
    } catch {
        Write-Warning "Crash-event collection failed: $($_.Exception.GetType().Name)"
    }
    throw
} finally {
    $PackageCleanupFailure = $null
    if ($Installed) {
        try {
            # WACK can register the package again. Only this test's family/version
            # is eligible for cleanup, and preflight rejected existing installs.
            $Residual = @(Get-AppxPackage -Name $IdentityName -ErrorAction Stop |
                Where-Object { $_.PackageFullName -eq $Installed.PackageFullName })
            foreach ($Copy in $Residual) {
                Get-PackageProcesses "InferBridge.Native" $Copy.InstallLocation |
                    Stop-Process -Force -ErrorAction SilentlyContinue
                Get-PackageProcesses "InferBridge" $Copy.InstallLocation |
                    Stop-Process -Force -ErrorAction SilentlyContinue
                Remove-AppxPackage -Package $Copy.PackageFullName -ErrorAction Stop
            }
        } catch {
            $PackageCleanupFailure = "Could not remove the test MSIX from this user: $($_.Exception.Message)"
            Write-Warning $PackageCleanupFailure
        }
    }
    if ($Trusted) { Remove-Item $Trusted.PSPath -ErrorAction SilentlyContinue }
    if ($Certificate) { Remove-Item $Certificate.PSPath -ErrorAction SilentlyContinue }
    try {
        Remove-Item -LiteralPath $Work -Recurse -Force -ErrorAction Stop
    } catch {
        Write-Warning "Could not remove temporary signed MSIX artifacts from $($Work): $($_.Exception.Message)"
    }
    Write-Host "Evidence: $EvidenceDirectory"
    # The smoke test must not report success when its own package remains.
    if ($PackageCleanupFailure) { throw $PackageCleanupFailure }
}
