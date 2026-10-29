# Native WinUI ↔ web capability inventory

Source of truth: live `dev` code, not the older release notes. Review this inventory
whenever native UI, FastAPI contracts or release packaging change. "Full native"
describes comparable user functionality, **not** proof that a signed installer has
passed Windows hardware certification.

| Domain | Native status | Evidence and intentional limitations |
|---|---|---|
| Chat, model switching and streaming | Full native for the shared subset | `MainWindow.xaml.cs`, `MainWindow.ChatRendering.cs`, `MainWindow.ChatTools.cs`, `BackendClient.cs`: SSE, stop waiting, message-level copy, per-chat system prompt; non-stop SSE finishes cannot enter saved history |
| Chat model bar: Eject and the load guard | Native and web | `MainWindow.ChatModelBar.cs`, `ChatSession.cs`: **Eject** beside the Chat model picker (Ctrl+Shift+E) unloads the current model through `POST /v1/models/unload` and keeps the conversation; an ejected model is not silently reloaded by the next send (`_ejectedModelId`). `BackendClient.UnloadModelAsync` retries only the engine's 409 "busy" refusal, never other refusals. Loading a model while others are loaded asks **Load another model?** (keep loaded / unload others / cancel), like the web model-lifecycle guard. Not yet exercised on Windows hardware |
| Regenerate | Native and web | `MainWindow.ChatModelBar.cs`, `ChatSession.cs`: **Regenerate** resends the history without the newest assistant reply, with the current generation settings |
| Context budget chip, omitted turns and reduce reply | Native and web | `ChatSession.cs`, `MainWindow.ChatModelBar.cs`: the chat bar chip asks the engine's `POST /v1/chat/context-budget` with exactly the body the next chat request would send, shows the exact prompt/reply budget, lists turns the engine would omit, and offers **Reduce reply to fit** (lowers max reply tokens) and **Check again** |
| Saved chat defaults | Native and web | `NativePreferences.cs`, `MainWindow.GenerationSettings.cs`: **Save as my defaults** / **Use engine defaults** for max tokens, temperature and top-p, stored in a small local preferences file (the browser uses localStorage); no sync between the two |
| Theme | Native and web | `NativePreferences.cs`, Settings → Appearance (`AppThemePicker`): Use Windows setting, Light or Dark, saved locally |
| Starter prompts and keyboard shortcuts | Native and web | `MainWindow.ChatModelBar.cs`: starter prompts on an empty chat (a click fills the prompt, never sends); Esc stops generation and Ctrl+L starts a new chat, matching the browser footer shortcuts |
| Engine activity feed | Native and web | `ChatSession.cs`, `MainWindow.ChatModelBar.cs`: **Recent engine activity** card reads the engine event log (`GET /v1/events?limit=100`), newest first, bounded and sanitized; manual Refresh |
| Hardware advisor ("Best model for this PC") | Native and web | `HardwareAdvisorClient.cs`, `MainWindow.HardwareAdvisor.cs`: per-profile recommendation from the engine advisor (device, precision, context, output, fit score, reason, warnings, measured tok/s and TTFT when available, synthetic results labelled), a generation-only model table sorted by fit, and explicit **Download and load** / **Show model** actions. It never switches the chat model on its own |
| Chat Markdown rendering | Same safe subset on both sides | `ChatMarkdown.cs` mirrors `renderMarkdown` in `web/index.html`: ATX headings (h1–h6), fenced code with copy-code, thematic breaks, flat bullet/ordered lists, block quotes, inline code, strong and emphasis. Links, images and raw HTML stay literal text. Neither native nor web implements full CommonMark |
| Vision image attachments | Native, capability-gated | `MainWindow.ChatTools.cs`: attachments are only offered when the engine reports `supports_vision` for the selected model (`GetModelSupportsVisionAsync`) and are sent as OpenAI `image_url` content parts; actual image understanding depends on the backend and model |
| Conversation management | Full native for the shared subset | `NativeConversationStore.cs`, `MainWindow.ConversationFlags.cs`: opt-in local search, rename, delete, restore, pin/unpin, archive/restore (separate archived view), duplicate ("… copy", unpinned, active), Markdown export and `inferbridge-conversation` JSON export/import (4 MiB limit, same as web). Pinned/archived flags round-trip through the JSON format (only a literal `true` sets one, like the browser) and default to off for older history files. Retention never removes a pinned chat; a history made only of pinned chats refuses new saves instead of dropping one. Native keeps its stricter 40-chat / 20-turn limits. JSON files are a manual exchange format; there is no automatic native↔browser history sync |
| Catalog model discovery and download | Full native | `MainWindow.xaml.cs`, `BackendClient.cs`: curated catalog, preparation progress, retry |
| Verified Model Library import/export/adopt/refresh | Full native via the engine | `ModelLibraryClient.cs`, `MainWindow.ModelLibrary.cs` (Model library → **Import / export**): **Check for newly verified models…** (confirmed `POST /v1/model-library/refresh`, the web Refresh: the engine downloads the official manifest from the trusted GitHub URL, adds/updates verified definitions and never replaces user definitions or downloads model files; a 502 is reported as "could not be downloaded or failed validation"), definitions export (`GET /v1/model-library/export`, default or all registered), confirmed definitions import (`POST /v1/model-library/import-definitions`, 1 MB / 50 definitions, always `overwrite=false`), detected managed-folder adoption (`/unregistered-managed`, `/adopt-managed`) and converted-directory import (`/import-converted`), with the web form's fields. The engine keeps validating IR markers, links, disk space and active-model conflicts. Definition files that set `trust_remote_code` are refused natively (import them in the web UI). Responses are parsed strictly; a lost response after a long copy is reported, not retried. Not yet exercised against real model folders on Windows hardware |
| Hugging Face search and custom downloads | Full native for supported options | `MainWindow.HuggingFace.cs`, `HuggingFaceClient.cs`: account/search/token, conversion queue and conversion options (backend, weight format, group size, ratio, symmetric quantization, device, context/output tokens, load-after). `trust_remote_code` is deliberately always `false` from native; models that need remote code must use the web UI |
| Model conversion and recovery | Partial native | `MainWindow.ModelPreparation.cs`, `ModelPreparationClient.cs`: guarded recovery and cancellation |
| Inference and generation configuration | Partial native | `MainWindow.GenerationSettings.cs`: request-only tokens/temperature/top-p; the web chat settings now also send `top_p` (persisted locally). KV cache precision is per model in the context editor (CPU/GPU only, never NPU); no arbitrary runtime options; Windows hardware validation pending |
| Per-model context window | Native and web | One shared native editor (`ContextEditor.cs` rules, `MainWindow.ContextEditor.cs` dialog, `ContextConfigurationClient.cs`) on every Model library card (Context… and Load with context…), the Chat model bar and Inference settings pane (Change… beside Context used), and the Settings list of all models. Browser parity in the Model Library cards (`app/model_library_ui.py`). Works without loading the model; presets capped at the model maximum (plus **max for this model**) and custom values, trial flag, KV cache precision picker (Auto/f16/u8/u4, CPU/GPU only) with precision-aware KV estimate, NPU/CPU/GPU context guidance, Apply now vs next load. Applying keeps the editor open with a progress ring and locked controls ("Applying 16K context… rebuilding engine" / "Saving 16K context…"), mirrors the busy line on the card, Chat model bar and pane, pauses chat sends during the rebuild, then shows "Context set to 16K" (or restores the controls with the error); the browser editor and composer do the same. Guide: `docs/CONTEXT_AND_MEMORY.md`. Backend `GET /v1/models/contexts`, `GET/POST /v1/models/context`, `POST /v1/models/load` `context_tokens`. Awaits a Windows run on real hardware |
| Hardware/device inspection and chat target selection | Native and web, hardware verification pending | `GetDevicesAsync` exposes detected CPU/GPU/NPU and backend-suggested AUTO/MULTI/HETERO combinations. Chat offers an explicit load/switch request, with requested target distinct from the engine-reported active device; real multi-device behavior is experimental and unverified. Web parity: `web/index.html` offers only detected direct devices, AUTO and backend suggestions; selecting sets the next load target, the Load button performs the in-place `/v1/models/load` switch (`app/ui_quality.py`), and the 'Next load target · Reported active device' line keeps the two distinct |
| Benchmark run history and comparable results | Full native for history | `BenchmarkHistoryClient.cs`, `MainWindow.Benchmark.cs`: authenticated bounded history, per-row measurement and strict compatibility; actual Windows execution awaits CI |
| Native layout, charts and status (2026-10 redesign) | Native-only presentation | `docs/NATIVE_UI_DESIGN.md`, `ChartControls.cs`, `ChartMath.cs`, `AdaptiveLayout.cs`, `MainWindow.Shell.cs`, `MainWindow.ModelCards.cs`, `MainWindow.ChatStats.cs`: grouped navigation (Settings in the footer; "Engine" renamed **Engine & devices**), pane-footer status pills, card grids, Chat inference pane (generation settings moved from Settings), chat stats strip from client timing plus engine `include_usage` tokens, model cards with per-row actions, benchmark tok/s and TTFT bars plus trend, doctor level counts and RAM gauges. Charts plot only engine/client-measured values. Not yet checked on Windows at 125–150% scaling, high contrast or with Narrator |
| Benchmark Lab matrix and progress | Native and web | `MainWindow.Benchmark.cs`, `BenchmarkLabClient.cs`: multiple prepared generation models, discovered direct devices and AUTO routing, Quick/Standard/Thorough presets, bounded eight-combination runs and operation-correlated engine-event progress; missing/truncated events remain indeterminate and no server-side cancellation API exists. Web (`app/advisor_ui/script_lab.py`): same presets, 8-combination ceiling, `client_run_id`-correlated engine progress, 4xx/409 refusals, uncertain lost responses confirmed only through history refresh, no server cancel |
| System Doctor and model health | Full native for existing remediation | native Doctor screen, `RuntimeHealthClient.cs`, `MainWindow.Maintenance.cs`: core checks, privacy-safe report, and model-health remediation through the existing protected `/v1/runtime-health/action` and `/v1/runtime-health/batch` endpoints with explicit confirmation |
| Storage/cache inventory and cleanup | Full native | `GetStorageInventoryAsync` and guarded cleanup; engine rechecks permissions |
| Hugging Face authentication | Full native | `HuggingFaceClient.cs`: backend-controlled credential storage, verification and removal (as permitted) |
| Local Connection Hub | Native for details and self-test | `ConnectionHubClient.cs`, `MainWindow.ConnectionHub.cs`: verified dynamic loopback port, authentication and LAN labels, usable loaded models, explicit model selection, secret-free Python/curl examples and individually reported engine-owned synthetic self-tests |
| Connect a tool (coding-assistant setup) | Native and web; one-click native only | Native `ToolSetup.cs`, `MainWindow.ToolSetup.cs`; web `TOOL_SETUP_JS` in `app/connection_hub_ui.py`: the same seven client cards, values, steps, snippets, agentic context note and self-test button in both. Only the native app writes Continue `config.yaml` or Copilot CLI user variables, behind preview, explicit confirmation, backup and Undo; the browser stays copy-only. Store edition offers PowerShell instead of writing virtualized HKCU variables. Awaits a Windows run with real clients |
| Network and API listener configuration | Full native via the engine | `ConnectionHubClient.cs`, `MainWindow.Maintenance.cs`: reads and changes listener/LAN, CORS and API-key settings through the same protected `/internal/desktop-network` endpoint the browser uses (verified origin, `X-OV-LLM-UI` header, confirmation for risky choices such as wildcard CORS). Firewall rules stay outside the app |
| Engine restart | Full native | `POST /v1/desktop/operations/restart-server` with confirmation (tray controller restarts the engine; refused while a benchmark or model preparation runs); cached network state is discarded afterwards |
| Diagnostics and support export | Full native | `GetDesktopOperationsAsync`, `ExportDiagnosticsAsync`: server-owned ZIP generation, explicit confirmation |
| Model preparation recovery | Full native for supported actions | `ModelPreparationClient.cs`: backend-owned action restrictions and recovery IDs |
| NPU load-failure recovery (#65) | Native and web | Engine classifies an NPU compile rejection (`app/errors.py`) and returns `recovery_actions`; web model card buttons (`web/index.html` `runModelRecovery`) and native model cards (`MainWindow.ModelCards.cs`, `BackendClient.cs`) offer **Update Intel NPU driver**, **Load on GPU instead** and **Reconvert for NPU (channel-wise INT4)**. Never switches device automatically. Covered by `tests/test_npu_load_recovery.py` and `NpuRecoveryContractTests.cs`; not yet exercised on Meteor Lake hardware |
| Update settings and checks | Full native | `ReleaseUpdateClient.cs`: reads/saves `/desktop/release/settings`, checks and skips releases; release links are restricted to official release URLs (`IsOfficialReleaseUrl`). Installing an update is still handed to the official installer/package |
| App settings and startup | Partial native | native Settings, `app/startup_registration.py`: local runtime/UX controls; web controls some advanced preferences |
| Onboarding wizard | Partial native | The browser has a step-by-step first-run wizard (`app/onboarding_ui.py`, Restart Onboarding). Native has no wizard; it covers the same ground with the engine onboarding recommendation and the **Best model for this PC** card on the Engine & devices page |
| Hugging Face gated-model access | Partial native | Native stores and verifies the token and shows the publisher-license notice before a download (`MainWindow.HuggingFace.cs`); the browser's dedicated gated-access preflight dialog (`app/huggingface_access_ui.py`) has no native equivalent |
| Embedding models | Native and web for models; no playground on either side | Both sides can download, convert, register and load embedding models (`openvino-embeddings`); the engine serves `/v1/embeddings`. Neither the browser nor native has an embedding playground or test UI |

### Remaining intentional gaps

- Web-only: `trust_remote_code` Hugging Face conversions and model definitions that enable it (deliberate, for security: native never enables remote repository code).
- Left out of native on purpose: per-message timestamps and per-message stats (native keeps the last-reply stats strip), queue-a-message-while-loading (native Send stays disabled while a model operation runs), and a jump-to-latest button.
- Partial native: the onboarding wizard and the Hugging Face gated-access dialog (see rows above).
- Neither side: full CommonMark, an embedding playground, automatic native↔browser conversation and preference sync.

## Windows launch qualification inventory

| Supported entrypoint | Source contract | Evidence status |
|---|---|---|
| Inno installer post-install, Start Menu and desktop shortcuts | `packaging/installer.iss` uses `native\\InferBridge.Native.exe` | Source-inspected; installed runtime smoke not observed for this change |
| Root portable / installed `InferBridge.exe` | `app/frozen_entrypoint.py` forwards argument-free frozen launch to native; helper args run tray/server | Source-inspected; full packaged GUI launch not observed |
| Portable native directory | `build_dev_package.ps1`, `build_release.ps1` publish self-contained native files | Source-inspected; real portable GUI smoke not observed |
| MSIX main application and StartupTask | `packaging/msix/AppxManifest.xml` targets native WinUI executable | Source-inspected; manual installed MSIX smoke/WACK not observed |
| Per-user Windows startup | `app/startup_registration.py` registers native executable and migrates engine-first startup | Source-inspected; logon migration test not observed |
| Tray default Open / explicit Web UI | `app/tray_menu.py`, native Engine page | Source-inspected; tray/foreground behavior not observed |
| Single native foreground window | `windows-native/InferBridge.Native/Program.cs` redirects duplicate activation | Source-inspected; second-window Windows smoke not observed |
| Engine shutdown and upgrade | `EngineLauncher.cs`, `MainWindow.xaml.cs`, `packaging/installer.iss` | Source-inspected; upgrade with persisted models/settings not observed |

GitHub's [`python312-windows.yml`](../.github/workflows/python312-windows.yml)
builds the native WinUI source on `dev` and runs the .NET contract harness;
the manually queued [Azure DevOps pipeline](../azure-pipelines.yml) builds
installer, portable and MSIX evaluation candidates with bundled engines.
The installed MSIX smoke test and Windows certification kit remain available
as local scripts, but are not automatically run by Azure.
Build checks and actual user-machine CPU/GPU/NPU qualification are different
forms of evidence. Never infer a passed package smoke test from a successful
source, XAML compile or mocked-backend contract test.

## Next parity milestone

Run the Local API native page and message renderer on Windows 11 at 150% DPI
with Narrator and a real loaded Intel CPU/GPU model. Check that direct API
clients still require their own key, that model switching and engine restart
invalidate stale Hub state, and that an interrupted self-test is not silently
treated as a passed generation. The new source/HTTP contract tests do not
qualify an installed package or a physical device.

Also exercise, on real Windows hardware, the new native parity surfaces:
network/listener changes through `/internal/desktop-network` (including LAN
exposure and API-key rotation), engine restart, model-health remediation,
update settings, conversation JSON export/import round-trips with the browser,
an image attachment against a model that reports `supports_vision`, conversation
pin/archive/duplicate with a browser JSON round-trip, and Verified Model Library
definition export/import, detected-folder adoption and converted-directory import
against real OpenVINO IR folders, and the 2026-10 chat parity surfaces (Eject,
the load guard, Regenerate, the context budget chip, saved defaults, theme,
starter prompts, Esc/Ctrl+L, the engine activity feed, the hardware advisor and
Check for newly verified models). These
are covered by source and mocked-HTTP contract tests only; none of them has
been qualified on Windows hardware or in an installed package.

### Existing Benchmark Lab qualification

Qualify the native Benchmark Lab on a Windows machine using local WinUI XAML
compilation and the contract-test executable, followed by real hardware
CPU/GPU/NPU runs. Native benchmark requests use the existing protected
`POST /v1/benchmarks/run` and engine event stream, with no independent
persistence. A lost POST response is an uncertain outcome and disables retries
for that window. A strict client-generated run identifier is persisted in the existing
engine history, so manual history refresh can confirm a completed uncertain run;
absence from bounded history never proves the operation was rejected. Competing
API requests get HTTP 409 instead of silently queueing. A session restart
invalidates selections and progress. Closing the window stops native polling,
not the engine benchmark; no server cancellation endpoint exists. Synthetic
mock results are not measurements of physical CPU, GPU or NPU hardware. Follow with advanced inference settings and desktop-wide startup
migration verification. Do not add a native inference implementation.
