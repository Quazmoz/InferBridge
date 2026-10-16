# InferBridge native Windows client

The **C# / WinUI 3 / Windows App SDK** application that the Microsoft Store
package launches. It renders XAML native controls, not HTML or WebView2. The
Python FastAPI/OpenVINO engine (`InferBridge.exe`) remains the source of truth
for models, credentials, inference and process ownership; this client talks to
it over verified loopback HTTP.

## What it does

- **Chat**: the picker lists downloaded and loaded models with their state.
  Selecting a downloaded model loads it through the engine; chat stays disabled
  until the engine reports it loaded. Live SSE streaming chat, Ctrl+Enter to send,
  copyable conversation text and cancellation. Unsaved history stays in memory
  (20 recent messages, bounded prompt budget). Chat previews appear as tokens arrive, but an incomplete, failed or cancelled reply is removed instead of saved into the in-memory conversation. Cancelling the HTTP request
  does not prove server-side inference has stopped.
- **Saved conversations**: an opt-in native history toggle persists completed user/assistant exchanges as bounded, readable JSON in this installation's data root (`native-conversations.json`), separate from the web browser's local history. The toggle is off for new installations; existing saved files re-enable it on restart. A native picker restores saved conversations with their original model identity; local-only search matches titles, model IDs, and message text. Rename preserves an edited title across subsequent completed replies, and a confirmed Delete action permanently removes a selected chat. Only completed streaming turns are committed. Writes use a temporary file and same-folder atomic replacement; unreadable or malformed history is never overwritten silently. Storage is **not encrypted**, not synced, and may be read by other processes with the same user account's filesystem permissions. Disable saving to stop new writes; delete individual saved conversations to remove them. Up to 40 conversations / 20 turns per conversation are retained.
- **Model library**: the engine's catalog with readable states. The engine's
  hardware-based recommendation is listed first and preselected.
  **Download and load** uses the engine's convert route (download, convert,
  then load; it also retries a failed or cancelled preparation). **Load**, **Unload**, and confirmed **Delete local files** use the existing authenticated model APIs. Deletion is available only for unloaded, downloaded models and rechecks live model state before mutation. Progress comes from the
  engine's own status, refreshed every 8 seconds; the client never invents it.
  The engine's own refusal reasons (for example gated Hugging Face access) are
  shown as given, bounded to plain text.
- **Model recovery**: the native Model library can inspect sanitized interrupted-preparation details, review the engine's allowed actions, resume or retry interrupted work, restart from download, or remove incomplete output. The selected recovery ID and action permissions are rechecked against the verified backend immediately before the request; stale recovery is rejected. Destructive cleanup requires a second explicit confirmation. The backend owns all cleanup and conversion; the native client never deletes model directories or caches directly.
- **System Doctor**: dedicated WinUI health page uses the same authenticated `/v1/system/status` and `/v1/devices` reads as the browser doctor. It reports OpenVINO runtime mode, CPU/GPU/NPU visibility, active routing/fallback, model conversion and errors, RAM and model-volume storage thresholds, plus recommended fixes. Checks are re-runnable; the native clipboard action copies only an allowlisted support summary without credentials, prompts, model errors or local paths. Mock mode is explicitly shown as unverified hardware.
- **Operations & diagnostics**: a native WinUI screen reads the same verified desktop-operations status used by the web GUI: application version, installation mode, controller availability, server state, startup preference and benchmark state. A separately confirmed action requests the engine's existing privacy-sanitized diagnostic ZIP export. **Run quick benchmark** measures one 32-token generation for the exact currently loaded model and actual device (reverified before posting), with measured decode speed, TTFT and latency when available; mock results are synthetic. The native client never reads private tray-control credentials or downloads the archive into its process.
- **Engine**: connection status, **Start local engine**, and a session-only
  API-key field (a PasswordBox). The key stays in this window's memory and is
  never written to settings, process arguments, model files or browser storage.
- **Web UI**: a persistent notice and **Open web UI** button show that advanced
  settings, benchmarks and more features are available in the browser. The button
  re-verifies the local engine and opens its loopback URL without keys in the URL.

## Feature parity tracking

The web GUI remains available independently. The native Windows app is real WinUI/XAML, not a WebView2 rendering of it. Current native coverage includes chat (bounded SSE streaming with cancellable partial previews), model selection/download/load/unload/delete and guarded preparation recovery, inference target selection, device scanning, engine connection, quick benchmark execution, native System Doctor checks and sanitized clipboard reports, opt-in persistent saved conversations with local search and rename, and operations/diagnostics export.

Still missing from the native frontend: browser/native history synchronization, rich Markdown/code rendering, local vision and embeddings workflows, Hugging Face account management, advanced System Doctor per-model details and remediation actions, multi-model benchmark comparisons/presets and history, context budget planning, storage manager, network/API administration, and other advanced settings. A present backend route does **not** imply that its native interface is implemented. Parity work should reuse existing authenticated backend services and keep the two frontends independently functional.

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
- The window's **X** (or Alt+F4) asks whether to **Close window only**, **Quit
  InferBridge**, or **Cancel**. Closing only cancels this window's requests and
  keeps the engine, tray, downloads and web UI running. Quitting asks the tray to
  stop the whole program through its existing same-user command file and waits
  for its instance lock to be released. The window stays open on a shutdown
  failure or timeout. The native client never reads private tray credentials or
  kills processes. A pending launcher start is awaited before requesting quit.

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
recommendation read, payload and SSE limits, UTF-8 streaming, truncated/error responses, cancellation, chat-picker states and graceful
tray quit (including a stopped server and a pending engine start). It runs on any OS with
the .NET 10 SDK (add `-p:EnableWindowsTargeting=true` off Windows):

```powershell
dotnet run --project .\windows-native\InferBridge.Native.ContractTests\InferBridge.Native.ContractTests.csproj -c Release
```

`tests/test_native_windows_source_contract.py` guards the XAML controls and
key safeguards. The **Native Windows frontend** workflow now runs on manual
dispatch rather than every `dev` push. Its standard run builds and publishes a
Windows evaluation artifact. Dispatched with *full_combined_candidate*, it also
builds the Store MSIX and runs
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

## Inference hardware selection

In the native **Model library**, select **Inference device** before choosing **Load** or **Download and load**. The options are **Automatic (engine default)**, **NPU**, **GPU**, and **CPU**. This device preference is included in the existing model-load or conversion-and-load API request; it is not a separate inference engine. Automatic omits an explicit target, allowing the server to use its configured default. Hardware availability and model compatibility are still enforced by the Python/OpenVINO backend; selecting NPU or GPU does not guarantee it is supported on the PC or for the selected model. Change device by unloading and reloading the model. The chat model picker continues to select models rather than moving already loaded models between devices.

The native **Engine** page also offers **Scan inference devices**, using the authenticated `/v1/devices` endpoint. The model-library selector then lists detected CPU/GPU/NPU targets (and Automatic); hardware discovery errors disable explicit-device selection rather than implying unsupported accelerators are usable. Detection does not certify model compatibility.

The Engine hardware view also reads `/v1/system/status` to show which device each loaded model is **actually using**, rather than confusing the requested inference target with the runtime device.
