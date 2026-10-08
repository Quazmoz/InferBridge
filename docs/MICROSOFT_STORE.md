# Microsoft Store (MSIX)

The Store build is the zero-cost way to ship InferBridge without SmartScreen or "unknown publisher" warnings: Microsoft re-signs the MSIX with its own certificate after certification. Individual developer registration is free.

## What the package is

`scripts/build_msix.ps1` packs the same installed-mode PyInstaller layout the Inno Setup installer ships into an unsigned `InferBridge-<version>-windows-x64.msix`. `packaging/msix/AppxManifest.xml` is the template and `scripts/msix_package.py` fills it in.

Behavior that differs from the installer build, all in `app/msix.py` callers:

| Area | Store build |
|---|---|
| Data root | Same `%LOCALAPPDATA%\InferBridge`; write virtualization is disabled, so models survive reinstalling and are shared with an installer copy. |
| Start with Windows | Declared as a manifest StartupTask, off by default. Users toggle it in Windows Settings > Apps > Startup; the in-app toggle explains this instead of writing the Run key. |
| Updates | Delivered by the Store. GitHub update checks are disabled and the release dialog says so. |
| Uninstall | Removes the program; user data stays, as with the installer's default. |

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
4. Under *Submission options > Restricted capabilities*, justify `runFullTrust` (desktop app) and `unvirtualizedResources` ("keeps multi-gigabyte AI model downloads in the user's existing InferBridge data folder so they survive reinstalls and are shared with the standalone installer").

Local build on Windows with the Windows SDK:

```powershell
.\scripts\build_release.ps1 -Unsigned -SkipPortable -MockSmokeTest
.\scripts\build_msix.ps1 -Version <version> -IdentityName <name> -Publisher "CN=..." -PublisherDisplayName "<name>"
```

To sideload-test before submitting, sign a copy with a self-signed certificate whose subject equals the publisher value; the Store submission itself must stay unsigned.
