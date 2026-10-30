# Microsoft Store (MSIX)

The Store build is the zero-cost way to ship InferBridge without SmartScreen or "unknown publisher" warnings: Microsoft re-signs the MSIX with its own certificate after certification. Individual developer registration is free.

## What the package is

The Store previously rejected InferBridge because its primary interface was a web page in a WebView2/browser shell. The package now launches a **native C# / WinUI 3 application** (`windows-native/InferBridge.Native/`) with XAML Chat, Model library and Engine screens. It does not embed a web page.

| Package path | Role |
|---|---|
| `native\InferBridge.Native.exe` | The application entrypoint (Start menu, taskbar). Self-contained .NET 10 + Windows App SDK. |
| `InferBridge.exe` | The installed-mode PyInstaller engine and tray. The native window starts it on launch; it owns the OpenVINO server, models and lifecycle. |
| `Assets\*.png`, `resources.pri` | Store logo, app icon, small/medium/wide/large tiles and splash screen at 100/125/150/200/400% scale, plus plain and `altform-unplated` taskbar icons (16–256 px). They are generated from `scripts/generate_brand_assets.py` by `scripts/msix_package.py` and indexed with MakePri. |

`scripts/build_msix.ps1` publishes the native client (or takes `-NativePublishDirectory`), `scripts/msix_package.py` validates both payloads and fills `packaging/msix/AppxManifest.xml`, and MakeAppx packs an unsigned `InferBridge-<version>-windows-x64.msix`.

Behavior that differs from the standalone installer build:

| Area | Store build |
|---|---|
| Primary UI | Native WinUI window. The tray's **Open** action opens the same native window; a second launch focuses it. |
| First run | The window starts the engine, then the Model library leads with the engine's hardware-based recommendation and offers **Download and load**. |
| Data root | `%USERPROFILE%\InferBridgeStoreData` (outside MSIX AppData virtualization). Survives uninstall; not automatically shared with standalone editions. |
| Start with Windows | Manifest StartupTask (off by default) executes `native\InferBridge.Native.exe` directly; WinUI then starts the engine. Users toggle it in Windows Settings > Apps > Startup. |
| Updates | Delivered by the Store. The engine refuses GitHub update checks, the native About card hides every update control and download link (detected from package identity before the engine answers), and the tray's **Check for Updates** item is hidden. |
| Uninstall | Removes the program; model downloads and settings under `%USERPROFILE%\InferBridgeStoreData` remain until explicitly deleted. |

The MSIX version is `<major>.<minor>.<patch>.0`; the Store reserves the fourth field. Pre-release suffixes are dropped, so submit only stable versions.

## One-time setup

1. Register free at https://storedeveloper.microsoft.com as an individual developer (ID and selfie check).
2. Read the reserved Partner Center identity (*Product management > Product identity*) and supply those values to `build_msix.ps1`. `packaging/msix/AppxManifest.xml` uses substitution tokens, not hardcoded identity values:
   - Name `QuinnFavo.InferBridge`
   - Publisher `CN=CAC9A06A-996E-4DC7-9079-45803515E811`
   - PublisherDisplayName `Quinn Favo`
   - Store ID `9PJ0NSD01070`

   These are public identifiers, not secrets. The build accepts `-IdentityName`, `-Publisher`, and `-PublisherDisplayName`, or the `INFERBRIDGE_MSIX_IDENTITY_NAME`, `INFERBRIDGE_MSIX_PUBLISHER`, and `INFERBRIDGE_MSIX_PUBLISHER_DISPLAY_NAME` environment variables. Confirm all values against the current Partner Center registration.

## Each release

1. Manually queue the [Azure DevOps Windows pipeline](../azure-pipelines.yml)
   against the reviewed revision. Under **Artifact output type**, choose
   `msix` for an unsigned Store package, `all` for MSIX plus installer/ZIP,
   or another output type as described below. Verify the queue-time public
   Partner Center identity, publisher, and publisher display name.
2. Download the Azure pipeline artifact and verify `azure-selected-checksums.txt`.
   Azure builds and verifies the full native-first release source payload first,
   then packages the same installed-mode WinUI + Python engine as an MSIX.
   The pipeline does **not** install the MSIX or sign it.
3. Run `scripts/smoke_test_msix.ps1` against the real candidate to verify
   installed package identity, foreground window, engine handoff, and relaunch.
   Add `-RunCertificationKit` to execute WACK and review the evidence.
4. Only after a candidate passes installation and certification, upload the
   unsigned Store package through Partner Center. Complete the listing,
   age rating, and privacy policy URL; explain that first model download
   requires an internet connection and may take several minutes.
5. The MSIX requests only `runFullTrust`. If an older submission lists
   `unvirtualizedResources`, remove it.
6. Fill the listing from [STORE_LISTING.md](STORE_LISTING.md): description,
   features, keywords, what's new, screenshots, age rating, the `runFullTrust`
   justification and the privacy policy URL. Declare live generative AI
   (Store Policies 11.16) in both the description and the submission. The app's
   **Report a reply** link (Chat) and **Help & feedback** link (Settings > About)
   are the in-product reporting path.

Local example (run from a clean, reviewed checkout; substitute the correct
version and registered Partner Center identity):

```powershell
.\scripts\build_release.ps1 -Version <version> -Channel auto -Unsigned -SkipPortable -MockSmokeTest
.\scripts\build_msix.ps1 -Version <version> -IdentityName <name> -Publisher "CN=..." -PublisherDisplayName "<name>"
.\scripts\smoke_test_msix.ps1 -Package <path-to-candidate.msix> -EvidenceDirectory artifacts\msix-smoke -RunCertificationKit
```

The sideload smoke test requires an elevated Windows PowerShell 5.1 session
with InferBridge closed; it uses a temporary test certificate and removes
the package and certificate afterward. The Store submission must stay unsigned.
No GitHub Actions workflow builds a releasable package. On `dev`, the Windows
source qualification workflow packs a *structural* MSIX from the real WinUI
publish and a placeholder engine with the Windows SDK's MakePri and MakeAppx,
which validates the manifest, every logo and `resources.pri`; it is never uploaded.
The Azure build validates source and package structure, **not** installed MSIX
behavior, Store certification, or real inference. A `dev` or `beta` MSIX is an
evaluation candidate only; submit only a reviewed stable-version package
after the installed smoke/certification procedure.

## Azure DevOps: choose the output at queue time

In Azure DevOps, select the InferBridge YAML pipeline, choose **Run pipeline**,
set the source branch (`dev` for evaluation; `main` only after separately
approved promotion), and choose the **Artifact output type** parameter:

| Value | Downloaded binaries |
|---|---|
| `installer-portable` (default) | Unsigned Inno Setup `.exe` and portable `.zip`, with the complete release evidence (unchanged behavior) |
| `installer` | Unsigned installer `.exe` |
| `portable` | Portable `.zip` |
| `msix` | Unsigned native WinUI Microsoft Store `.msix` |
| `all` | Installer `.exe`, portable `.zip`, and Store `.msix`, with complete release evidence |

**Release channel** defaults to `auto`: the channel follows `app/version.py` on the
selected commit (no suffix -> `stable`, `beta`/`rc` -> `beta`, `dev` or another
suffix -> `nightly`). For example, `1.1.0` builds stable, `1.1.0-beta.1`
builds beta, and `1.1.1-dev.1` builds nightly **regardless of branch name**.
Choose `stable`, `beta` or `nightly` only to override it; an explicit channel
that does not fit the version fails instead of silently producing the wrong
artifact. The run log prints the resolved version, channel and branch.

**Release blocker (2026-10-30):** the current `main` revision still declares
`1.1.1-dev.1`, not a stable version. Do not build a new Store submission from
that revision as though it were a stable release. Fix and qualify the launch
crash on `dev`, then separately promote a reviewed, correctly versioned stable
candidate before using `main` for Store packaging.

For the Store MSIX submission: source branch `main`, **Artifact output type**
`msix`, **Release channel** `auto` (or `stable`), and the default MSIX identity
fields after comparing them with Partner Center.

The optional MSIX fields default to the identity currently recorded above.
Compare them with **Partner Center > Product identity** before queuing.
No private signing key is requested. The pipeline deliberately remains manual;
it neither publishes a release nor uploads to Microsoft Store.

All choices use the same source-pinned full release build and existing provenance
verification. The selection happens **after** successful qualification. This
avoids silently weakening native, checksum or artifact-manifest gates for
single-format requests, but means selecting only MSIX still performs the
underlying installer/portable qualification build. The published pipeline
artifact retains its existing `inferbridge-windows-x64-unsigned` name.
Every selection contains `azure-selected-checksums.txt` and
`azure-build-provenance.json`; combined modes also retain the original full
release manifest and checksums.

## Storage and uninstall disclosure

The Store package uses `%USERPROFILE%\InferBridgeStoreData` for its model files,
conversion cache, configuration, and logs. This avoids the MSIX AppData virtualization
boundary without disabling registry or filesystem virtualization. InferBridge does not
write HKLM, modify Windows/system directories, or use this capability to access other
applications' data (the capability is not declared at all). User-selected model locations
and an explicit `OV_LLM_DATA_DIR` override remain possible.

Store uninstall does **not** remove `%USERPROFILE%\InferBridgeStoreData`. After
quitting InferBridge, users can manually delete that folder if they want to remove
models and private data. The standalone installer continues to use
`%LOCALAPPDATA%\InferBridge` by default; the two editions do not automatically
copy or migrate large downloads. To share, explicitly set a common
`OV_LLM_DATA_DIR` outside AppData and never run both editions concurrently.

## Certification rejection: 2026-10-30

Partner Center product ID `9PJ0NSD01070` failed on a **Microsoft Surface Laptop 4, Windows build 26300.9711**:
**10.1.2.10 Functionality** (crash at launch) and **11.11.1 Age Ratings** (questionnaire).
The supplied `InferBridge_CrashLog.evtx` identifies an **Application Error**
in `InferBridge.Native.exe` version `1.1.1.0`, with the bundled
`native\Microsoft.UI.Xaml.dll` version `3.2.3.0` as the faulting module,
exception `0xC000027B` (stowed exception), and offset `0x3A9DBD`. The event
does **not** contain the inner stowed error or a stack trace; it does not establish
which application XAML statement, if any, caused the fault.

The **module, version, exception and offset exactly match** Microsoft
[microsoft-ui-xaml#12169](https://github.com/microsoft/microsoft-ui-xaml/issues/12169),
which affected several Microsoft applications on Windows 11 26H2. That issue
concerns a *framework-dependent* runtime path, whereas the InferBridge MSIX
**bundles its own** XAML.dll under `native\`: a shared runtime defect is a
credible hypothesis, not a demonstrated identical root cause. The other
reporter's recovery after a restart had no confirmed causal explanation.

The `dev` remediation candidate upgrades `Microsoft.WindowsAppSDK` from
`2.5.1` to Microsoft's stable `2.6.4` (released 2026-10-27), disallows
trimming and single-file publication, and requires the self-contained
`Microsoft.UI.Xaml.dll` in package staging. **This is not yet proof that
the Store crash is fixed**: qualify the updated *installed MSIX* before
resubmitting. If it persists, collect the stowed exception with a private
user-mode dump/WinDbg instead of guessing at a XAML control.

On a Windows machine, extract the Microsoft ZIP and inspect the actual event log:

```powershell
.\scripts\inspect_store_crash.ps1 -EvtxPath "C:\path\InferBridge_CrashLog.evtx" -OutputPath "artifacts\store-certification-crash.json"
```

Review the output before sharing it; event messages may contain machine paths
and personal information. Record the faulting **process, module, exception code**
and the exact submitted MSIX version/build provenance. Fix the evidenced fault,
then rebuild and repeat the **installed**, package-identity launch smoke test
on a clean Windows 11 environment (no Visual Studio-installed SDK assumed),
including the Windows App Certification Kit. For local smoke-test failures,
`scripts/smoke_test_msix.ps1` captures bounded application/activation
event details in the chosen evidence directory.

The Store reviewer also requires **Yes** for **User Content Sharing** in
the age-ratings questionnaire. Revisit all IARC answers for accuracy and
regenerate the certificate in Partner Center; see
[STORE_LISTING.md](STORE_LISTING.md#age-rating-iarc-questionnaire).
This is a Partner Center action, not an MSIX manifest fix.

## Certification regression gate

Older Store evaluation evidence reported an overall WACK **PASS**, with optional
"Archive files usage" and "Blocked executables" package-sanity findings.
That result is **not** certification of the current `dev` source, its new native
Connection Hub/chat controls, or any newly built MSIX. Each candidate must
repeat installed MSIX smoke tests and WACK checks, retaining the actual report
before making a PASS claim. Optional heuristic findings must be reviewed for
the specific candidate rather than presumed harmless.

The automated smoke test covers installation, Start-menu-equivalent activation, a visible native window, the engine starting from inside the package with a verified session, and relaunch focusing the existing window. Before submitting, also check on a real Windows 11 PC with the signed sideload package:

- First run with no models: the recommended model downloads, loads, and answers in Chat.
- Keyboard-only navigation and Narrator across Chat, Model library and Engine; 150% display scaling; light and dark mode.
- The window's **X** offers **Close window only**, **Quit InferBridge**, and **Cancel**, with a clear explanation. Close-only leaves the tray, downloads and web UI running; the tray's **Open** reopens the native window. Quit stops the engine and tray before the native window exits; cancelling keeps everything running.
- Model data survives an update (install a higher version over it) and an uninstall/reinstall.
