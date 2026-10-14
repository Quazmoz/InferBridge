# Microsoft Store (MSIX)

The Store build is the zero-cost way to ship InferBridge without SmartScreen or "unknown publisher" warnings: Microsoft re-signs the MSIX with its own certificate after certification. Individual developer registration is free.

## Native Windows migration notice (October 2026)

**Store submission blocked; do not resubmit the current MSIX.** The previous
WebView2/pywebview shell was rejected because Microsoft requires the app's
primary functionality to open as an application, not merely a browser tab.
A genuine C# / WinUI 3 / XAML client is being developed under
`windows-native/InferBridge.Native/`. Its source now includes native chat,
session-only API-key entry, engine connectivity, model inventory and authenticated
load/unload controls. Windows runtime and packaged integration remain unverified.

The MSIX manifest **still launches the previous Python/WebView entrypoint**.
Until the native executable is Windows-build-verified, packaged alongside the
Python engine, designated the actual `<Application Executable>` and tested as
an installed Store-equivalent MSIX, certification is **not** resolved.
See `windows-native/README.md` for the migration qualification checklist.

## What the package is

`scripts/build_msix.ps1` packs the same installed-mode PyInstaller layout the Inno Setup installer ships into an unsigned `InferBridge-<version>-windows-x64.msix`. `packaging/msix/AppxManifest.xml` is the template and `scripts/msix_package.py` fills it in.

Behavior that differs from the standalone installer build:

| Area | Store build |
|---|---|
| Primary UI | **Transitional/blocked:** existing MSIX launches the Python WebView2 shell; the new WinUI 3 native client is not yet wired into MSIX. Do not submit the existing package. |
| Data root | `%USERPROFILE%\InferBridgeStoreData` (outside MSIX AppData virtualization). Survives uninstall; not automatically shared with standalone editions. |
| Start with Windows | Declared as a manifest StartupTask, off by default. Users toggle it in Windows Settings > Apps > Startup; the in-app toggle explains this instead of writing the Run key. |
| Updates | Delivered by the Store. GitHub update checks are disabled and the release dialog says so. |
| Uninstall | Removes the program; model downloads and settings under `%USERPROFILE%\InferBridgeStoreData` remain until explicitly deleted. |

The MSIX version is `<major>.<minor>.<patch>.0`; the Store reserves the fourth field. Pre-release suffixes are dropped, so submit only stable versions.

## One-time setup

1. Register free at https://storedeveloper.microsoft.com as an individual developer (ID and selfie check).
2. The reserved Partner Center identity (*Product management > Product identity*) is built into `release.yml`:
   - Name `QuinnFavo.InferBridge`
   - Publisher `CN=CAC9A06A-996E-4DC7-9079-45803515E811`
   - PublisherDisplayName `Quinn Favo`
   - Store ID `9PJ0NSD01070`

   These are public identifiers, not secrets. Repository variables `MSIX_IDENTITY_NAME`, `MSIX_PUBLISHER`, and `MSIX_PUBLISHER_DISPLAY_NAME` override them if the identity ever changes.

## Each release

1. Run **Release build** (`.github/workflows/release.yml`) with *msix* checked. SignPath signing is independent; uncheck *sign* if it is not set up yet.
2. Download the `InferBridge-<version>-msix` artifact.
3. In Partner Center, create a submission, upload the `.msix` under *Packages*, and complete the listing, age rating, and a privacy policy URL (the privacy statement in [CODE_SIGNING.md](CODE_SIGNING.md#code-signing-policy) works).
4. The MSIX requests only `runFullTrust` for its desktop process; the denied `unvirtualizedResources` request must be removed from the Partner Center submission.

Local build on Windows with the Windows SDK:

```powershell
.\scripts\build_release.ps1 -Unsigned -SkipPortable -MockSmokeTest
.\scripts\build_msix.ps1 -Version <version> -IdentityName <name> -Publisher "CN=..." -PublisherDisplayName "<name>"
```

To sideload-test before submitting, sign a copy with a self-signed certificate whose subject equals the publisher value; the Store submission itself must stay unsigned.

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

## Certification regression gate

Before creating a new submission, sideload a *signed, package-identity-bearing* MSIX
on Windows 11. Start from the Start menu and confirm a visible InferBridge-owned
application window, not merely a browser tab. Exercise chat (including no-model
onboarding), model library, server stop/recovery, tray reopen and repeated launch;
verify model data after an update and uninstall/reinstall. Test missing WebView2
runtime as a recoverable error. Source-level unit tests or an unpackaged executable
alone do not establish this acceptance criterion.



## Optional native payload integration (development only)

`scripts/build_msix.ps1 -NativePublishDirectory artifacts\native-win-x64`
includes an already-published WinUI 3 x64 client under `native\` in the
unsigned MSIX mapping alongside the installed-mode Python distribution.
This is opt-in so that the existing Store entrypoint and release behavior do
not change without signed installation evidence. The native CI workflow checks
the mapping helper against real Windows x64 publish output. Python regression
tests cover mapping collisions, missing native entrypoint and manifest identity.

**This is not a Store-ready native app:** the manifest application and startup
task still launch the Python entrypoint. Before switching either entrypoint,
sign and sideload a candidate on Windows 11 with actual package identity;
exercise first launch, engine handoff, background lifecycle, reconnect,
repeated launch, protected APIs, data preservation, upgrade/uninstall, and
WACK. Retire Python UI dependencies only after proving they have no remaining
consumer.


## Opt-in combined native launch evaluation

The native MSIX evaluation mode is separate from the existing Store packaging
workflow. Run scripts/build_msix.ps1 with -NativePublishDirectory and
-NativeEntrypointEvaluation after building the installed Python distribution
and a complete self-contained WinUI publish. Only the generated candidate
manifest launches native\InferBridge.Native.exe, including the disabled startup
task. The committed AppxManifest.xml and production release workflow are
unchanged. The unsigned candidate carries an explicit evaluation filename
and must never be submitted to Partner Center.

The Native Windows frontend GitHub Action supports a manual
full_combined_candidate dispatch from dev. It builds both real components,
packs them using MakeAppx, then inspects archive contents and the executable
entrypoint. Source-level packaging tests run on regular dev pushes.
See windows-native/README.md for exact build commands.

Even a successful combined build remains unqualified for signed installation,
first launch, packaged backend discovery, authenticated operations, crash and
sleep recovery, device inference, upgrade/uninstall data preservation, Windows
App Certification Kit, and Microsoft Store certification.
