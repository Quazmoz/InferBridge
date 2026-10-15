# InferBridge native Windows client

The **C# / WinUI 3 / Windows App SDK** application that the Microsoft Store
package launches. It renders XAML native controls, not HTML or WebView2. The
Python FastAPI/OpenVINO engine (`InferBridge.exe`) remains the source of truth
for models, credentials, inference and process ownership; this client talks to
it over verified loopback HTTP.

## What it does

- **Chat**: non-streaming chat with a loaded model, Ctrl+Enter to send,
  copyable conversation text and cancellation. History stays in memory
  (20 recent messages, bounded prompt budget). Cancelling the HTTP request
  does not prove server-side inference has stopped.
- **Model library**: the engine's catalog with readable states. The engine's
  hardware-based recommendation is listed first and preselected.
  **Download and load** uses the engine's convert route (download, convert,
  then load; it also retries a failed or cancelled preparation). **Load** and
  **Unload** use the existing authenticated model APIs. Progress comes from the
  engine's own status, refreshed every 8 seconds; the client never invents it.
  The engine's own refusal reasons (for example gated Hugging Face access) are
  shown as given, bounded to plain text.
- **Engine**: connection status, **Start local engine**, and a session-only
  API-key field (a PasswordBox). The key stays in this window's memory and is
  never written to settings, process arguments, model files or browser storage.

## Engine handoff and safety

- When the bundled engine sits beside `native\` and no verified session
  exists at launch, the window requests one engine start (the Python tray's
  instance lock still arbitrates ownership). A pending start stops blocking
  retries after 90 seconds without a verified service.
- Before any API call the client reads bounded session metadata and verifies
  the loopback port and instance nonce via `/desktop/instance`, then
  `/health/live`. Proxies and redirects are disabled and all requests target
  literal `127.0.0.1`. A nonce check is not an OS-level identity guarantee
  against a malicious same-user process.
- Polling pauses while the engine rejects the entered API key, so it never
  trips the engine's invalid-key throttle (HTTP 429); a throttled engine is
  reported as verified-but-refusing, not offline.
- One window per installation directory: another launch focuses the existing
  window (restoring it only if minimized) without starting a second engine
  owner. Portable installations in different folders stay independent.
- Closing the window cancels its requests; the tray keeps the engine running.
  The native client never owns engine shutdown or private tray credentials.

Session metadata discovery follows the Python tray's data roots: signed MSIX
uses `%USERPROFILE%\InferBridgeStoreData`; an unpackaged installation uses
`%LOCALAPPDATA%\InferBridge` (or the existing legacy `OpenVINOWindowsLLM`
root); a combined portable distribution with a sibling `portable.flag` uses
`<portable root>\data`. `OV_LLM_DATA_DIR` is honored, and
`INFERBRIDGE_NATIVE_DATA_DIR` overrides metadata lookup only and takes
precedence. Overrides must be absolute; relative ones fail closed.
The native engine launcher passes `OV_LLM_DATA_DIR` as an explicit `--data-dir`
argument because the Python desktop launcher resets inherited launch flags.

## Development build

Windows 10 19041+ or Windows 11 x64, with the .NET 10 SDK:

```powershell
dotnet build .\windows-native\InferBridge.Native\InferBridge.Native.csproj -c Release -p:Platform=x64
dotnet run --project .\windows-native\InferBridge.Native\InferBridge.Native.csproj -p:Platform=x64
```

For a source build, start the Python desktop engine separately (the
**Start local engine** button only targets a bundled sibling `InferBridge.exe`)
and, if needed, set `INFERBRIDGE_NATIVE_DATA_DIR` to its data root.

## Tests

The dependency-free contract harness compiles the production
`BackendClient.cs` and `NativeDataPaths.cs` with an in-memory HTTP transport
and temporary directories. It covers data-root resolution, nonce/port
verification, stale and malformed metadata, redirects, API-key challenges and
throttling, guarded load/unload/download, engine refusal messages, the
recommendation read, payload limits and cancellation. It runs on any OS with
the .NET 10 SDK (add `-p:EnableWindowsTargeting=true` off Windows):

```powershell
dotnet run --project .\windows-native\InferBridge.Native.ContractTests\InferBridge.Native.ContractTests.csproj -c Release
```

`tests/test_native_windows_source_contract.py` guards the XAML controls and
key safeguards. The **Native Windows frontend** workflow builds and publishes
the client on every relevant `dev` push; dispatched manually with
*full_combined_candidate*, it also builds the Store MSIX and runs
`scripts/smoke_test_msix.ps1` (install, package-identity launch, native
window, verified engine session, single-window relaunch, screenshots) and the
Windows App Certification Kit.

## Packaging

For a local **EXE installer** that opens the native app and bundles the engine,
install Inno Setup 6 and run from the repository root:

```powershell
.\scripts\build_native_installer.ps1 -Python .\.venv\Scripts\python.exe
```

The unsigned evaluation installer and SHA-256 checksum are written to
`artifacts/native-dev/`. It runs the native contract harness, the pinned engine
build and packaged smoke tests before compiling the installer. Start-menu and
optional desktop shortcuts open `native\InferBridge.Native.exe`; the engine
bootstrap check still runs before Setup offers to launch it. It uses the existing
InferBridge installation identity, so installing upgrades an existing EXE
installation while preserving models and settings. This does not publish a release.

`scripts/build_msix.ps1` publishes this project self-contained for win-x64
and maps it under `native\` next to the installed-mode engine; the manifest's
application entrypoint is `native\InferBridge.Native.exe`. See
[docs/MICROSOFT_STORE.md](../docs/MICROSOFT_STORE.md) for the release and
submission steps.
