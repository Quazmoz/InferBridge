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
- LAN access disabled by default

Launching the shortcut starts the tray controller, which owns the FastAPI child server and opens the existing browser UI through loopback. The packaged listener defaults to `127.0.0.1`. Explicit authenticated LAN access can be enabled later from **Network / API access** without modifying files in the installation directory. Closing the browser does not stop the application.

Start with Windows is enabled later from the tray. It creates one HKCU Run value and starts the tray/server in the background without opening the browser.

## Network and firewall behavior

The installer does not create an unrestricted Windows Firewall rule. LAN mode is opt-in and requires API authentication. Windows may prompt when the listener begins accepting LAN traffic; users should allow InferBridge only on trusted **Private** network profiles.

A GUI-managed API key is stored under the writable InferBridge data root using Windows DPAPI. Installed-mode network preferences and encrypted credentials remain under `%LOCALAPPDATA%\InferBridge`, outside `%LOCALAPPDATA%\Programs\InferBridge`.

## Build

```powershell
.\scripts\build_windows_distribution.ps1
```

Use `-SkipInstaller` to create only the portable ZIP when Inno Setup is unavailable. Artifacts are versioned, checksummed, and accurately marked signed or unsigned.

Before compiling the installer, the release pipeline runs the packaged executable without `portable.flag`, verifies that it reports `installed` mode, and exercises the full mock API, UI, lifecycle, benchmark, and owned-shutdown contract. The extracted portable ZIP is then tested separately in `portable` mode. Actual installer installation, authenticated LAN binding, Windows Firewall prompting, upgrade, downgrade, and uninstall behavior still require validation on a clean Windows machine before release.

## Upgrade and uninstall

Installer upgrades replace application files only. Mutable data remains under `%LOCALAPPDATA%\InferBridge`.

Interactive uninstall asks whether to retain downloaded models, settings, logs, benchmarks, onboarding state, and diagnostics. Preservation is the default. Desktop network preferences and the encrypted API key follow the same retained user-data policy. Disable Start with Windows from the tray before uninstall when possible; the per-user Run value can also be removed manually.

See [LAN and home-lab access](LAN_ACCESS.md) for the packaged network configuration and firewall model.
