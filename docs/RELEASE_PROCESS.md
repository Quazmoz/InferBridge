# Production release process

Local Windows release generation is the primary workflow. GitHub Actions performs lightweight source validation, but it does not build, sign, or publish production releases automatically.

## Prerequisites

- Windows 10 2004 or newer, x64
- Python 3.11 or newer
- Git
- Inno Setup 6 when building the installer
- Windows SDK `signtool.exe` for signed releases
- a trusted Authenticode code-signing certificate for stable publication
- GitHub CLI only when publishing

Update `app/version.py`, add `docs/releases/<version>.md`, commit the source, and ensure the working tree is clean.

## Build

Stable production release:

```powershell
.\scripts\build_release.ps1 -Version <version> -Channel stable -Clean -Sign -MockSmokeTest -GenerateChecksums
```

Unsigned local validation:

```powershell
.\scripts\build_release.ps1 -Version <version> -Unsigned -SkipInstaller -MockSmokeTest
```

Tests may be skipped only with the explicit `-SkipTests` flag. Dirty source is rejected unless `-AllowDirty` is supplied, and that state is recorded in the manifest.

The build deterministically generates a 512-pixel PNG and multi-resolution Windows ICO. The ICO is embedded in the PyInstaller launcher and Inno Setup installer. The packaged browser client uses the shared SVG for its favicon and header identity.

Artifacts use deterministic names under `artifacts\release-<version>`:

```text
OpenVINO-Windows-LLM-<version>-windows-x64-installer.exe
OpenVINO-Windows-LLM-<version>-windows-x64-portable.zip
OpenVINO-Windows-LLM-<version>-checksums.txt
OpenVINO-Windows-LLM-<version>-release-manifest.json
OpenVINO-Windows-LLM-<version>-third-party-licenses.zip
OpenVINO-Windows-LLM-<version>-release-notes.md
```

The release environment installs pinned top-level requirements from `requirements/release.txt`. Each build records the fully resolved `pip list` and `pip freeze` results. This records exact release inputs without claiming byte-for-byte reproducibility across Windows SDK, Python, compiler, or timestamp changes.

## Signing

Preferred certificate-store configuration:

```powershell
$env:OV_LLM_SIGNTOOL_PATH = 'C:\Program Files (x86)\Windows Kits\10\bin\...\x64\signtool.exe'
$env:OV_LLM_SIGN_CERT_SHA1 = '<certificate thumbprint>'
$env:OV_LLM_SIGN_TIMESTAMP_URL = 'http://timestamp.digicert.com'
.\scripts\build_release.ps1 -Version <version> -Channel stable -Clean -Sign -MockSmokeTest -GenerateChecksums
```

PFX fallback:

```powershell
$env:OV_LLM_SIGN_CERTIFICATE = 'C:\secure\release-signing.pfx'
$env:OV_LLM_SIGN_CERTIFICATE_PASSWORD = '<set only in the current secure environment>'
$env:OV_LLM_SIGN_TIMESTAMP_URL = 'http://timestamp.digicert.com'
.\scripts\build_release.ps1 -Version <version> -Channel stable -Clean -Sign -MockSmokeTest -GenerateChecksums
```

Certificates and passwords must never enter the repository or logs. The build marks an artifact signed only after `signtool verify /pa /all` succeeds. Timestamp, signing, or verification failure blocks a signed release.

Use exactly one certificate source. Supply all certificate values only through the secure job environment; never use command-line arguments, checked-in configuration, build metadata, or release notes. PFX signing requires a nonempty environment-supplied password and must not prompt interactively. The `/tr` and `/td SHA256` arguments request an RFC 3161 timestamp.

`-Sign` requires both portable and installer outputs. After the build verifies each binary, the publisher independently validates the manifest and summary claims and reruns:

```powershell
signtool verify /pa /all <installer.exe>
signtool verify /pa /all <launcher-extracted-from-portable.zip>
```

Any incomplete claim, missing SignTool, malformed archive, missing binary, or failed verification blocks publication. Stable publication also rejects a release that makes no signed claim. The publisher does not consume certificate material.

The code-signing certificate subject becomes the Windows publisher identity. A valid trusted signature removes the `Unknown publisher` state. SmartScreen reputation is a separate Microsoft reputation signal, so a newly signed product or certificate may still receive a caution screen until reputation develops.

## Verification

```powershell
Get-FileHash .\OpenVINO-Windows-LLM-<version>-windows-x64-installer.exe -Algorithm SHA256
python .\scripts\release_tools.py verify-checksums --path .\OpenVINO-Windows-LLM-<version>-checksums.txt
```

Confirm the generated icon appears on the installer file, installed launcher, Start Menu shortcut, optional desktop shortcut, Apps and Features uninstall entry, browser tab, and browser header. Do not treat source SVG or ICO inspection as proof that Windows resources were embedded correctly.

Mock smoke validation proves packaged contracts, not real CPU, GPU, NPU, drivers, installer upgrade behavior, Authenticode trust, or SmartScreen reputation unless those paths were separately executed.

## Clean-machine signed upgrade and uninstall

Run this only after both SignTool checks pass in the secure build environment:

1. Create a disposable, fully updated Windows 11 x64 VM with no repository checkout, certificate, Python environment, prior app data, or trusted development roots.
2. Snapshot the VM. Download the previously published release and the candidate signed release through their GitHub release assets; verify both checksum files.
3. Install the previous release per-user, launch it, complete mock onboarding, create non-secret settings and benchmark state, and stop it cleanly.
4. Before execution, run `signtool verify /pa /all` on the candidate installer and on the launcher extracted from its portable ZIP. Retain sanitized command output.
5. Confirm Windows displays the certificate publisher rather than `Unknown publisher`. Record any separate SmartScreen reputation warning accurately.
6. Install the signed candidate over the previous version. Confirm version, startup, retained mutable data, server and tray health, browser branding, shortcut icons, and packaged mock contract.
7. Uninstall from Windows Settings. Confirm binaries, shortcuts, startup registration, and uninstall registration are removed. Record whether the documented user-data retention choice was honored.
8. Revert the VM snapshot and test a fresh signed install and uninstall separately.
9. Sanitize the report for usernames, hostnames, full profile paths, emails, tokens, prompts, generated text, certificate secrets, and raw logs before publication.

Do not mark upgrade, uninstall, icons, or signing verified from a development machine, source test, mock-only build, or unsigned artifact.

## Publish

```powershell
.\scripts\publish_release.ps1 -Version <version> -Channel stable -DryRun
.\scripts\publish_release.ps1 -Version <version> -Channel stable
```

The publisher validates the canonical version, clean tree, expected artifact names, checksums, provenance, duplicate tag, duplicate GitHub release, and signatures before creating an annotated tag and release. Stable publication requires verified Authenticode signatures for both the installer and portable launcher. Beta and nightly releases are marked pre-release and may be used for explicitly identified unsigned validation, but they must not be described as signed.

Published tags and release assets are immutable. Already published unsigned releases must never be replaced or retagged. A later signed build must advance the version.
