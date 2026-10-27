# InferBridge native Windows client

The **C# / WinUI 3 / Windows App SDK** application that the regular Windows installer, portable ZIP and Microsoft Store package launch. It renders XAML native controls, not HTML or WebView2. The
Python FastAPI/OpenVINO engine (`InferBridge.exe`) remains the source of truth
for models, credentials, inference and process ownership; this client talks to
it over verified loopback HTTP.

## Layout and navigation

The window uses a grouped left `NavigationView` rail — **Chat**; *Models*:
**Model library**, **Hugging Face**; *Engine*: **Engine & devices**,
**Benchmark Lab**, **System Doctor**, **Local API**, **Operations**, **Storage
& cache**; **Settings** in the footer — with engine status pills (connection,
server address, loaded models and their engine-reported devices) in the pane
footer. Pages share a header (brand rule, title, subtitle, icon command row
with an accent primary action), responsive card grids (1/2/3 columns below
640 / 1008 px of content width) and in-house, theme-aware charts. See
[docs/NATIVE_UI_DESIGN.md](../docs/NATIVE_UI_DESIGN.md) for the design brief.

## What it does

- **Chat**: the picker lists downloaded and loaded models with their state.
  Selecting a downloaded model loads it through the engine; chat stays disabled
  until the engine reports it loaded. Chat offers a separate **Run model on**
  picker and explicit **Load / switch** action: engine default, AUTO routing,
  detected CPU/GPU/NPU devices, and backend-suggested composite AUTO/MULTI/HETERO
  targets. MULTI/HETERO are labelled experimental; OpenVINO and the model decide
  whether they work. The current model's *reported active device* is shown
  separately from the next requested target. A switch to an already-loaded model
  uses the engine's existing rollback-safe load handoff, never a second runtime.
  Device choices are session-only. Live SSE streaming chat, Ctrl+Enter to send,
  copyable conversation text and cancellation. Unsaved history stays in memory
  (20 recent messages, bounded prompt budget). Chat previews appear as tokens arrive, but an incomplete, failed or cancelled reply is removed instead of saved into the in-memory conversation. Cancelling the HTTP request
  does not prove server-side inference has stopped.
  Completed turns use native selectable text controls for headings, inline
  emphasis, quotes, bullets and fenced code blocks; individual messages and
  fenced code have copy buttons. The renderer executes no HTML, scripts, remote
  images or links. It is a safe Markdown subset, not full CommonMark parity;
  streaming previews stay plain text until a successful terminal stream event.
- **Chat stats and inference pane**: a stats strip under the composer shows the last completed reply's first-token and total time (measured by this client around the stream), engine-reported prompt/completion tokens (requested with `stream_options.include_usage`) and decode tokens/s computed as in the engine benchmark; a ring gauge shows context used against the model's reported window. Anything the engine does not report reads "not reported". The **Inference settings** pane beside the conversation (inline from 900 px, overlay when narrower) holds the system prompt and Slider/NumberBox pairs for temperature, top-p and max tokens.
- **Native Settings**: the **Inference settings** pane in Chat applies max generated tokens (1–4096 native cap), temperature (0–2), and top-p (0–1) to *future messages of the active native conversation* using the existing authenticated SSE route. Defaults (512, 0.7, 1.0) match FastAPI. Those request-only settings are memory-only and reset on a new/restored chat or model switch. The Settings page keeps a **Per-model context window** card, which reads live options from `/v1/models/context`, shows prompt/output budgets and illustrative FP16 KV-cache estimates when architecture allows them, and changes context via the same backend's authenticated `POST`. Context preferences persist in the engine's model catalog across restarts; a loaded model is rebuilt with rollback before handoff. Applying a context choice does not send a chat request, change existing messages, or persist secrets. Large contexts and hardware allocation remain unverified until tested. Unsupported top-k, repetition penalties, KV-cache quantization and device-wide settings are not exposed.
- **Saved conversations**: an opt-in native history toggle persists completed user/assistant exchanges as bounded, readable JSON in this installation's data root (`native-conversations.json`), separate from the web browser's local history. The toggle is off for new installations; existing saved files re-enable it on restart. A native picker restores saved conversations with their original model identity; local-only search matches titles, model IDs, and message text. Rename preserves an edited title across subsequent completed replies, and a confirmed Delete action permanently removes a selected chat. Only completed streaming turns are committed. Writes use a temporary file and same-folder atomic replacement; unreadable or malformed history is never overwritten silently. Storage is **not encrypted**, not synced, and may be read by other processes with the same user account's filesystem permissions. Disable saving to stop new writes; delete individual saved conversations to remove them. Up to 40 conversations / 20 turns per conversation are retained.
- **Model library**: the engine's catalog with readable states. The engine's
  hardware-based recommendation is listed first and preselected.
  **Download and load** uses the engine's convert route (download, convert,
  then load; it also retries a failed or cancelled preparation). **Load**, **Unload**, and confirmed **Delete local files** use the existing authenticated model APIs. Deletion is available only for unloaded, downloaded models and rechecks live model state before mutation. Progress comes from the
  engine's own status, refreshed every 8 seconds; the client never invents it.
  When the Model library is visible, the separate native progress strip reads the
  engine's versioned `/v1/models/status` snapshot and reports the selected model's
  current preparation phase and reported percentage (or explicitly notes when no
  percentage is available). Selected model details are also drawn from that same engine snapshot: declared backend, Hugging Face source (only when it has a safe repository ID), weight format, configured device preference, current runtime device, reported context/output limits, and vision support. Unknown fields are labelled *Not reported*, not inferred from model names or assumed to be compatible. **Cancel current preparation** is enabled only if the
  engine reports a cancellable operation ID. After confirmation, the client
  rechecks that same operation ID and capability before one authenticated
  `/v1/models/cancel` request; a conflicting or ambiguous response never causes
  automatic retry. Native OpenVINO loading may be non-interruptible; the engine
  has the final authority, and incomplete files can require recovery.
  The engine's own refusal reasons (for example gated Hugging Face access) are
  shown as given, bounded to plain text.
- **Model library import / export**: the **Import / export** menu mirrors the web Verified Model Library footer. It exports definitions (maintained and imported, or all registered), imports a definitions JSON after confirmation (1 MB / 50 definitions, never overwriting a different or active model), registers converted OpenVINO folders the engine detects in its managed models directory (kept in place), and imports an absolute OpenVINO IR directory as a managed copy. All requests use the protected `/v1/model-library/*` endpoints; definition files that enable `trust_remote_code` are refused and stay a web-only choice. Registration is not compatibility evidence: load and benchmark the model first.
- **Saved conversations**: with local history enabled, the saved-conversations flyout can pin/unpin (pinned first, never removed by retention), archive/restore (shown with **Show archived conversations**) and duplicate a chat, matching the web conversation manager. JSON export/import carries the browser's pinned/archived flags.
- **Storage & cache manager**: a native WinUI page reads the same desktop-only managed storage inventory as the browser: converted models, shared Hugging Face caches, incomplete preparation files, compiled OpenVINO cache, currently reclaimable bytes, and protected transaction backup totals. Only engine-authorized cleanup actions are enabled. Each cleanup requires a specific confirmation and a fresh engine-side capability and size recheck; the engine performs deletion with its existing path, symlink/junction, and lifecycle safeguards. The native client never selects arbitrary filesystem paths, follows links, or removes protected transaction backups.
- **Hugging Face account, discovery & preparation**: the dedicated native WinUI page reads account state, verifies a configured token, adds/replaces a read-scoped token via an ephemeral password dialog, and confirms removal of a locally stored token. All credential verification and DPAPI persistence remain in the existing Python backend; environment-managed tokens cannot be removed from the client. The page searches the same Hugging Face endpoint used by the WebGUI for text-generation, embedding, and image-text models and displays bounded model metadata. Model links open in the external browser without credentials. **Configure download** opens a scrollable native dialog for a local model ID, name, weight format, context budget, detected CPU/GPU/NPU target, and optional post-download loading. The client rechecks the device and existing catalog before submitting to the engine's existing `/v1/models/download-custom` queue, always with `trust_remote_code=false`. A timeout is never automatically retried because registration might already have succeeded. Results are remote candidates, **not** certified for local hardware, license terms, compatibility, memory or storage requirements. Model library shows actual download/conversion/loaded state.
- **Model recovery**: the native Model library can inspect sanitized interrupted-preparation details, review the engine's allowed actions, resume or retry interrupted work, restart from download, or remove incomplete output. The selected recovery ID and action permissions are rechecked against the verified backend immediately before the request; stale recovery is rejected. Destructive cleanup requires a second explicit confirmation. The backend owns all cleanup and conversion; the native client never deletes model directories or caches directly.
- **System Doctor**: dedicated WinUI health page uses the same authenticated `/v1/system/status` and `/v1/devices` reads as the browser doctor. It reports OpenVINO runtime mode, CPU/GPU/NPU visibility, active routing/fallback, model conversion and errors, RAM and model-volume storage thresholds, plus recommended fixes. Checks are re-runnable; the native clipboard action copies only an allowlisted support summary without credentials, prompts, model errors or local paths. Mock mode is explicitly shown as unverified hardware.
- **Model library cards**: each model row shows status, recommendation, running device, weight format, backend, context and vision pills, with per-row Load, Download and load, Unload, Delete files and (only when the engine health center recommends it) Revalidate. Row commands select the row and reuse the page handlers, so confirmations and guards are unchanged. A local filter hides rows without rebuilding the list.
- **Benchmark Lab**: a dedicated WinUI page charts decode tokens/s and first-token latency per model and actual device for the selected run, plus a best-decode trend across saved runs of the same real/synthetic mode (legacy inclusive rates are labelled and never mixed into the trend), and reads bounded, authenticated, engine-owned manual run history, shows saved per-model/device results and measured throughput, TTFT, total and load latency, process RSS and token counts; permits explicit comparison only for matching hardware fingerprints, method, run settings, model/source/precision and requested/actual device. Unavailable or incompatible values never become invented percentages. Full matrix configuration and engine-side progress remain web-only for now; the native quick benchmark is available under Operations.\n- **Operations & diagnostics**: a native WinUI screen reads the same verified desktop-operations status used by the web GUI: application version, installation mode, controller availability, server state, startup preference and benchmark state. A separately confirmed action requests the engine's existing privacy-sanitized diagnostic ZIP export. **Run quick benchmark** measures one 32-token generation for the exact currently loaded model and actual device (reverified before posting), with measured decode speed, TTFT and latency when available; mock results are synthetic. The native client never reads private tray-control credentials or downloads the archive into its process.
- **Engine & devices** (formerly Engine): detected-device pills, a RAM gauge and model-volume reading from the System Doctor status read, connection status, **Start local engine**, and a session-only
  API-key field (a PasswordBox). The key stays in this window's memory and is
  never written to settings, process arguments, model files or browser storage.
- **Local API**: a native Connection Hub reads the engine's authoritative
  loopback Base URL and dynamic port, authentication and LAN status, and loaded
  generation-capable models. Choose a model for secret-free Python SDK and
  `curl.exe` examples, or run the existing five-check synthetic self-test.
  Each result stays distinct. The native UI cannot enable LAN access or
  recover a stored API key; enter a session key on Engine & devices when required.
- **Web GUI (secondary)**: an optional link under **Engine & devices** opens the browser interface only on explicit user action. The button re-verifies the engine and uses its loopback URL without credentials. Chat, Model library and other native pages have no persistent browser promotion.

## Feature parity tracking

See [the maintained capability and Windows-launch qualification matrix](../docs/NATIVE_FEATURE_PARITY.md) for per-domain status and evidence.

| Capability | WebGUI | Native Windows | Evidence / remaining work |
|---|---|---|---|
| Chat and model load/unload | Implemented | Implemented | Native SSE, cancellation, chat-level CPU/GPU/NPU and suggested advanced routing plus explicit switch; real device combinations unverified |
| Model library and download of catalog models | Implemented | Implemented | Native uses engine convert and lifecycle routes; gated access applies to both |
| Live preparation stage, percentage and guarded cancellation | Implemented | Implemented in native controls | Uses `/v1/models/status` and identity-bound `/v1/models/cancel`; no native conversion queue; Windows qualification pending |
| Selected model configuration and active device details | Implemented | Implemented in native controls | Structured engine metadata; unknown fields safely hidden; device preference is not hardware proof |
| Interrupted model preparation recovery | Implemented | Implemented | Engine recovery ID and action rechecks; real Windows validation pending |
| Storage/cache inventory and guarded cleanup | Implemented | Implemented | Engine owns destructive cleanup; Windows CI qualification pending |
| Hugging Face account status/token verification/secure removal | Implemented | Implemented in native controls | Existing backend owns credentials; real HF account/DPAPI still need testing |
| Hugging Face remote model search | Implemented | Implemented in native controls | Search results are not evidence of compatibility, license approval, local download or load |
| Register a newly discovered remote model and convert | Implemented | Implemented in native controls | Native wizard supports bounded ID/name/weight format/context/device and engine queue; more advanced WebGUI conversion tuning still needed |
| Conversation history | Implemented | Implemented for the shared subset | Native saves/searches/renames/pins/archives/duplicates local history and exchanges JSON (with pinned/archived flags) with the browser; no automatic sync |
| Verified Model Library definitions export/import and local model adoption | Implemented | Implemented in native controls | Same `/v1/model-library/*` endpoints, `overwrite=false`; `trust_remote_code` definitions stay web-only; real IR folder qualification on Windows pending |
| System Doctor and runtime status | Implemented | Partially implemented | Native summary/report; advanced per-model remediation remains WebGUI-only |
| Benchmark Lab history and compatible-run comparison | Implemented | Implemented in native controls | Engine-owned history; no synthetic/real cross-comparison; Windows installed execution pending |
| Benchmark matrix configuration, progress, cancellation | Implemented | Native configuration/progress with limits | Native Benchmark Lab supports bounded matrix runs and correlated engine progress; no engine-side benchmark cancel route exists |
| Diagnostics export | Implemented | Implemented | Native sanitized engine export; no copying of private paths or tokens |
| Rich Markdown, multimodal/vision, embeddings interfaces | Implemented or partial in WebGUI | Partial native text/Markdown rendering | Native supports headings/emphasis and fenced code with copy actions; full CommonMark and image/vision attachment parity remain open |
| Request-scoped max tokens, temperature and top-p | Implemented in WebGUI | Native Settings implemented | Explicit Apply/Reset; memory-only, resets on new/restored chat or model switch |
| Model-aware generation controls | Backend-supported | Partial native | Checks reported embedding backend and output token cap; backend validates actual model/context |
| Per-model persistent context window | Implemented | Implemented in native Settings | Same backend GET/POST context APIs; engine rebuild and stale-update rollback; hardware qualification pending |
| Network/API and advanced runtime controls | WebGUI varies | Native read-only Local API Hub | Native displays dynamic endpoint, auth/LAN state, model IDs, copyable examples and five-part self-test; network mutation stays web-only |
| Engine HF search/access and model queue | Backend-supported | Exposed by both where above | Gated publisher approval, hardware and actual conversion require live verification |

**Qualification is outstanding** for the newest native account/search, custom-model registration, lifecycle progress/cancellation, recovery, and storage commits: the Azure manual build and real Windows tests must run on their final SHA. Mocked C# HTTP contracts and Python/XAML source tests are not substitutes for installing and exercising the WinUI app on Windows. Neither frontend includes a second inference engine or embeds the other.

**Native custom-model limitations:** the wizard deliberately excludes `trust_remote_code`, alternate conversion group-size/ratio/symmetric tuning, cancellation when the backend marks an operation non-cancellable, and compatibility claims. The WebGUI remains available for advanced conversion options. Re-selecting a previously queued model should happen in the engine-owned Model library, not by repeating the custom registration POST.

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

## Windows executable to open

The installed **InferBridge** Start Menu shortcut or `native\InferBridge.Native.exe` opens the real native WinUI 3 application. Windows startup registration also targets the native window; older engine-only registrations are migrated. The root `InferBridge.exe` is the Python engine/tray entrypoint, but native-first builds forward a no-arguments double-click to the WinUI app; explicit engine flags, including `--no-browser`, stay on the backend. Old browser-only installers lack the `native\` directory. An installer built from the updated `dev` release script must include it and use the native shortcut; installed execution still requires Windows qualification.

## Development build

Windows 10 19041+ or Windows 11 x64, with the .NET 10 SDK:

```powershell
dotnet build .\windows-native\InferBridge.Native\InferBridge.Native.csproj -c Release -p:Platform=x64
dotnet run --project .\windows-native\InferBridge.Native\InferBridge.Native.csproj -p:Platform=x64
```

To qualify native model details: select catalog, downloaded, and actively loaded models. Compare model backend, source, format, configured preference, actual device, context/output limits and vision capability against `GET /v1/models/status`. Confirm that unavailable device/metadata is labelled *Not reported*, not invented, and switching engine sessions does not display stale details or enable old cancellation permissions.

To qualify native cancellation: start a test model conversion on a Windows machine; in **Model library** select that model, verify reported phase and percentage, then request cancellation while the engine permits it. Confirm the matching operation ends in `cancelled`, recovery or retry remains available as appropriate, and a completed/native-compilation operation cannot be cancelled. Repeat across engine restart and with a second frontend advancing the same model to verify stale operation IDs fail closed. Do not use important in-progress model data for destructive testing.

To test native Hugging Face: start the engine, open **Hugging Face**, verify account status, add a disposable *read-scoped* user access token, test access, search a public model, open its page and review the repository. Select **Configure download**, confirm the device and settings, then queue the preparation and follow actual progress in **Model library**. Avoid large models when validating the first native flow; a successful queued response is not proof of successful conversion or inference. Disconnect the token and confirm the status is cleared. If the token comes from HF_TOKEN, removal remains disabled. For gated models, complete approval on the Hugging Face site; search metadata itself never proves permission or OpenVINO compatibility. Do not paste real tokens into issue reports or screenshots.

For a source build, start the Python desktop engine separately (the
**Start local engine** button only targets a bundled sibling `InferBridge.exe`)
and, if needed, set `INFERBRIDGE_NATIVE_DATA_DIR` to its data root.

## Tests

The dependency-free contract harness compiles the production
`BackendClient.cs`, `ConnectionHubClient.cs`, `HuggingFaceClient.cs`, and `NativeDataPaths.cs` with an in-memory HTTP transport
and temporary directories. It covers data-root resolution, nonce/port
verification, stale and malformed metadata, redirects, API-key challenges and
throttling, guarded load/unload/download, engine refusal messages, the
recommendation read, payload and SSE limits, UTF-8 streaming, truncated/error responses, cancellation, chat-picker states and graceful
tray quit (including a stopped server and a pending engine start). It runs on any OS with
the .NET 10 SDK (add `-p:EnableWindowsTargeting=true` off Windows):

```powershell
dotnet run --project .\windows-native\InferBridge.Native.ContractTests\InferBridge.Native.ContractTests.csproj -c Release
```

The Hugging Face contract cases exercise authenticated account requests, invalid and environment-managed tokens, token rejection redaction, bounded search metadata, and custom-model queue requests including device checks, duplicate IDs, gated access, cancellation, timeout-after-success and malformed responses with a mocked transport. They do not contact Hugging Face or verify DPAPI persistence.

`tests/test_native_windows_source_contract.py` guards the XAML controls and
key safeguards. On each `dev` change touching native sources, GitHub Actions
`python312-windows.yml` compiles the WinUI app and runs the native .NET contracts.
The manually queued Azure DevOps pipeline (`azure-pipelines.yml`) invokes
`scripts/build_release.ps1` to produce combined engine/native artifacts.
To qualify a Store MSIX separately,
run `scripts/build_msix.ps1` and `scripts/smoke_test_msix.ps1` on Windows;
the latter checks package installation/identity, native launch and relaunch,
and can optionally run the Windows App Certification Kit. These installed
checks are **not** part of the automatic Azure unsigned EXE/ZIP pipeline.

## Windowed UI acceptance check

After manually queuing the Azure DevOps build on `dev`, install or extract the
unsigned evaluation artifact and use a real Windows desktop with an existing
local engine. Check both the default and smallest permitted window sizes, at
100% and elevated display scaling when available:

1. **Chat:** send a message, then resize the window while streaming. The
   conversation list must stay visible and scroll independently, with Send,
   Cancel, model selection and the composer accessible. Open **Saved chats**
   to search, restore, rename and delete a saved conversation; closing the
   flyout must return to the same transcript. Ask for fenced multi-line code,
   emphasis, headings and literal HTML. Confirm safe native text rendering,
   selection, Copy code, Copy message, and absence of script/remote loading.
   Cancel mid-generation and verify no partial assistant turn is saved.
2. **Model library:** select a model and verify its inventory remains visible
   while the action/details section scrolls. Download/Load/Unload/Delete,
   Refresh, cancellation, and recovery must remain reachable without a
   horizontal scrollbar.
3. **Local API:** verify the Base URL matches the active nondefault listener
   port from desktop session metadata. Try authentication off and on (using a
   disposable local key), zero/one/multiple loaded chat models, explicit model
   selection, Python/curl placeholder copies, and all five self-test statuses.
   Restart the engine while the page is open and confirm stale values/results
   are invalidated. Confirm the page never enables LAN or firewall changes.
4. **Navigation and other screens:** collapse and expand the window; the
   navigation pane should adapt automatically (status pills hide with the
   pane). Engine & devices, Hugging Face, Benchmark Lab, System Doctor,
   Operations, Storage and Settings should reflow cards (3 → 2 → 1 columns) and
   scroll vertically without hiding controls or requiring sideways scrolling.
   Below 900 px the Chat inference pane must overlay, not squeeze, the transcript.
5. **High DPI and restart:** repeat the minimum-size and saved-conversation
   tests after changing Windows display scaling, then relaunch the native app.
   The selected model/history controls must still behave normally.

The XAML structural regression tests prevent the known layout classes from
returning, but **do not constitute pixel-level runtime or accessibility
qualification**. Azure's unsigned installer/ZIP job also does not run the
installed MSIX certification kit.

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
