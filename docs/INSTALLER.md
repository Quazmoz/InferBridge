# Windows installer guide

The Inno Setup configuration targets Windows x64-compatible systems on build 19041 or newer and installs the tray-enabled desktop build per-user.

## Installation behavior

- per-user installation under `%LOCALAPPDATA%\Programs\InferBridge`
- no administrator rights required for the normal path
- Start Menu shortcut launching the system-tray controller
- optional desktop shortcut
- Windows uninstall entry
- stable application ID for in-place upgrades
- models and mutable state stored outside the installation directory
- user data preserved by default during upgrades and uninstall
- Start with Windows disabled by default

Launching the shortcut starts the tray controller, which owns the localhost FastAPI child server and opens the existing browser UI. Closing the browser does not stop the application.

Start with Windows is enabled later from the tray. It creates one HKCU Run value and starts the tray/server in the background without opening the browser.

## Build

```powershell
.\scripts\build_windows_distribution.ps1
```

Use `-SkipInstaller` to create only the portable ZIP when Inno Setup is unavailable. Artifacts are versioned, checksummed, and accurately marked signed or unsigned.

Before compiling the installer, the release pipeline runs the packaged executable without `portable.flag`, verifies that it reports `installed` mode, and exercises the full mock API, UI, lifecycle, benchmark, and owned-shutdown contract. The extracted portable ZIP is then tested separately in `portable` mode. Actual installer installation, upgrade, downgrade, and uninstall behavior still require validation on a clean Windows machine before release.

## Upgrade and uninstall

Installer upgrades replace application files only. Mutable data remains under `%LOCALAPPDATA%\InferBridge`.

Interactive uninstall asks whether to retain downloaded models, settings, logs, benchmarks, onboarding state, and diagnostics. Preservation is the default. Disable Start with Windows from the tray before uninstall when possible; the per-user Run value can also be removed manually.
