# InferBridge native Windows frontend (development)

This is the **C# / WinUI 3 / Windows App SDK** replacement for the Store
edition's pywebview shell. It renders XAML native controls, not HTML or
WebView2. The existing Python FastAPI/OpenVINO engine remains the source of
truth for models, credentials, inference, and process ownership.

**Status: Phase 1 source scaffold on `dev`; not yet Store-qualified.**
The existing Store MSIX manifest still launches `InferBridge.exe` (the Python
tray/WebView path). Do **not** resubmit it as a native application yet.

## First implemented slice

- Responsive, native Windows navigation with Overview and Model library screens.
- Bounded, loopback-only HTTP inspection of the existing engine:
  - reads the bounded, user-owned `desktop-instance.json` metadata;
  - validates the port and server nonce via `GET /desktop/instance`;
  - checks `GET /health/live`;
  - shows the `GET /v1/models` inventory when API-key policy permits.
- Refuses redirects/proxies and uses finite timeouts and JSON size limits.
- Explicit user action to launch a bundled sibling `InferBridge.exe --no-browser`.
  Launch is **not** treated as proof that the backend is healthy.
- Detects a protected model inventory without bypassing the existing API key.

Not yet implemented: native API-key onboarding, chat, model downloads/load/unload,
hardware/benchmark controls, toast notifications, native single-instance
activation, Store manifest entrypoint, integrated MSIX packaging and signed
installed-package testing. No native UI test or Windows runtime validation
has been performed by repository code alone.

## Development build

Use Windows 11 x64 with .NET 10 SDK and the Windows App SDK build prerequisites.

```powershell
dotnet restore .\windows-native\InferBridge.Native\InferBridge.Native.csproj -p:Platform=x64
dotnet build .\windows-native\InferBridge.Native\InferBridge.Native.csproj -c Release -p:Platform=x64
dotnet run --project .\windows-native\InferBridge.Native\InferBridge.Native.csproj -p:Platform=x64
```

The project currently targets an **unpackaged, self-contained** evaluation
layout. Windows CI (`.github/workflows/native-windows.yml`) builds and
publishes evaluation output; it does not submit or install an MSIX.

In the combined future distribution, `InferBridge.Native.exe` and its runtime
files will live inside `native\` and Python `InferBridge.exe` at the package
root. The native `Start local engine` button targets that exact sibling binary.
For a source/developer build, run the Python desktop engine separately.

Native metadata root defaults to
`%USERPROFILE%\InferBridgeStoreData` to match the updated Store default.
For development against an existing standalone/server data directory, set
`INFERBRIDGE_NATIVE_DATA_DIR` to its absolute root before launching native
UI. The override changes **only metadata lookup**, never server hostname:
all requests use `127.0.0.1` and validate the nonce.

## Store migration acceptance

Before replacing the current MSIX entrypoint, verify on Windows:

1. Build the native project with the pinned SDK and confirm a genuine XAML
   window opens in both light/dark modes.
2. Launch a real packaged Python backend and exercise connected/disconnected,
   stale metadata, occupied/reused port, bad nonce, malformed/oversized JSON,
   API-key-protected reads and cancellation on close.
3. Implement native auth and essential actions without exposing an API key or
   private tray token in logs or process command lines.
4. Stage the self-contained native distribution plus Python backend, update
   MSIX to launch `native\InferBridge.Native.exe`, and retire pywebview **only**
   once the native path passes Windows installed-package smoke tests.
5. Validate Start-menu launch, one-window semantics, backend crash recovery,
   model persistence across update/uninstall, store policy, and the Windows App
   Certification Kit. Keep `unvirtualizedResources` absent.
6. Do not promote `dev` to `beta`/`main`, publish, or submit without explicit
   user approval and passing evidence.

See `docs/MICROSOFT_STORE.md` for the existing submission/release workflow.
