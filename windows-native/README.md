# InferBridge native Windows frontend (development)

This is the **C# / WinUI 3 / Windows App SDK** replacement for the Store
edition's pywebview shell. It renders XAML native controls, not HTML or
WebView2. The existing Python FastAPI/OpenVINO engine remains the source of
truth for models, credentials, inference, and process ownership.

**Status: native chat and model controls have passed the Windows x64 build/publish CI. Interactive native runtime and MSIX installation remain unqualified.**
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
- WinUI frontend single-instance activation scoped to the installation directory;
  another launch redirects to and focuses the existing native window, without
  creating a second engine owner. Portable installations in different folders
  retain independent native window instances.
- Explicit manual launch of the bundled Python engine; an 8-second status poll
  subsequently verifies its identity and health. A pending launch stops blocking
  retries after 90 seconds without verified service. The polling is a UI aid,
  not a replacement for the Python tray's lifecycle state machine. The native
  window does not own backend shutdown or private tray-control credentials.

**Not Store qualified.** The MSIX packaging helper can now *optionally*
include the native publish payload under `native\\` without changing the
Python launch entrypoint. This is an integration candidate, not a native Store
release; no signing, installed-package launch, or WACK has been demonstrated.
The default MSIX still starts the Python/WebView
interface. Native installation, interactive single-instance window behavior, packaged
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

Native metadata discovery now follows the authoritative Python tray's
distribution rules: signed MSIX uses `%USERPROFILE%\InferBridgeStoreData`;
an unpackaged installation uses `%LOCALAPPDATA%\InferBridge` (or the existing
legacy `OpenVINOWindowsLLM` root); and a combined portable distribution with
a sibling `portable.flag` uses `<portable root>\data`. The Python
`OV_LLM_DATA_DIR` override is honored. `INFERBRIDGE_NATIVE_DATA_DIR`
overrides metadata lookup only and takes precedence. Set overrides to absolute
paths; ambiguous relative overrides fail closed rather than attaching to an
incorrect instance. All HTTP requests remain bound to verified loopback
`127.0.0.1` and nonce identity. Neither backend storage nor models are
moved or duplicated by the native client.

## Windows 11 native lifecycle qualification (manual, not yet executed)

1. Build the native project using the commands above on Windows 11 x64.
   Run `InferBridge.Native.exe` from its complete published output folder.
   Confirm one native window, working navigation, and correct keyboard focus.
2. Launch the same executable a second time. Confirm no second native window
   remains, the original window is restored/focused, and chat history remains
   present. Confirm that two *separate* portable installation directories do
   not redirect to each other.
3. In an unpackaged development build, start the Python tray/backend separately
   and set `INFERBRIDGE_NATIVE_DATA_DIR` to its verified data root
   (normally `$env:LOCALAPPDATA\\InferBridge` for installed mode).
   Check connection, enter the real API key if configured, load a model,
   send a message and verify response/cancellation semantics.
4. In a combined evaluation layout (`native\\InferBridge.Native.exe` and
   sibling `InferBridge.exe`), start with the backend stopped. Use **Start
   local engine** once; the button should stay disabled while pending, and
   turn off automatically only when nonce/liveness verify (or after the bounded
   90-second unsuccessful startup). A second launch must not create a second
   tray/server lifecycle owner.
5. Simulate an unavailable engine and stale session metadata using a **temporary
   test data directory only**, never modifying live model stores. Ensure the
   client does not attach to an unverified listener and recovers when the
   authoritative tray has restarted. Close the native window during polling:
   no HTTP operations or status callbacks should outlive the window.
6. Repeat with the signed, identity-bearing MSIX after packaging integration.
   That test, update/uninstall preservation, and WACK are still required before
   any Store submission.

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

## Stage an optional combined MSIX integration candidate

After publishing the native x64 client and building the installed-mode Python
distribution, Windows developers can build an **unsigned transitional** MSIX:

```powershell
dotnet publish .\windows-native\InferBridge.Native\InferBridge.Native.csproj `
  --configuration Release --runtime win-x64 --self-contained true `
  -p:Platform=x64 -o artifacts\native-win-x64
.\scripts\build_msix.ps1 -Version <version> `
  -NativePublishDirectory artifacts\native-win-x64 `
  -DistributionPath dist\InferBridge
```

The native files are mapped under `native\` while `InferBridge.exe` remains
the Store entrypoint. The Python tray/engine continues owning lifecycle and
data. This staging does not replace signed MSIX installation testing or
constitute a Store-ready native package. Do not submit the transitional MSIX.

### Native Windows data-path contract tests

The Windows native workflow runs a dependency-free C# executable test harness
with temporary directories, covering Store, installed, legacy, portable,
override-precedence and unsafe relative-root cases:

```powershell
dotnet run --project .\windows-native\InferBridge.Native.ContractTests\InferBridge.Native.ContractTests.csproj -c Release
```

These are real .NET path-contract tests, not installed MSIX identity tests.
