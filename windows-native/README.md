# InferBridge native Windows frontend (development)

This is the **C# / WinUI 3 / Windows App SDK** replacement for the Store
edition's pywebview shell. It renders XAML native controls, not HTML or
WebView2. The existing Python FastAPI/OpenVINO engine remains the source of
truth for models, credentials, inference, and process ownership.

**Status: native chat and model controls implemented in source on `dev`; Windows build/packaging qualification pending.**
The existing Store MSIX manifest still launches `InferBridge.exe` (the Python
tray/WebView path). Do **not** resubmit it as a native application yet.

## Current native vertical slice

- Genuine WinUI 3/XAML Chat, Model library, and Engine screens.
- Session-only API-key entry using a native PasswordBox. The key stays in this
  window's memory; it is never written to application settings, process arguments,
  model files, or browser storage. Clear the field or close the window to stop using it.
- Bounded native non-streaming chat with loaded-model selection, Ctrl+Enter send,
  copyable conversation text, cancellation, timeouts, and clear recovery states.
  Conversation history is in memory only, limited to 20 recent messages and a
  bounded prompt budget. Cancelling the HTTP request does not prove server-side
  inference has stopped.
- Read-only model inventory with truthful backend states; explicit Load/Unload
  buttons use the existing authenticated model APIs. A backend-accepted load is
  not represented as complete until refreshed. Download, conversion, and progress
  bars are not independently fabricated by the native UI.
- Bounded metadata read and verified loopback instance port/nonce before
  browsing models or sending API mutations; proxy/redirect following disabled.
  A local nonce check is not an OS-level server identity guarantee against a
  malicious same-user process. Do not enable untrusted local programs.
- Explicit manual launch of the existing bundled Python engine. No native tray
  control token, duplicate lifecycle owner, or implicit stop on window closure.

**Not Store qualified.** The current MSIX still starts the Python/WebView
interface. Native installation, single-instance window activation, packaged
engine handoff, installed MSIX signing, and Windows certification tests remain
unverified. Never submit the transitional MSIX as a native application.

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

Chat requires the engine to have an active `loaded` model. From **Model library**,
select a `ready_to_load` model and choose **Load selected**, then refresh
until the backend reports `loaded`. Enter the API key on **Engine** if the
server rejects protected reads. Send with **Ctrl+Enter** or the Send button;
cancel only stops the client request, not necessarily ongoing backend compute.
Neither conversation history nor credentials persist across window sessions.

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
