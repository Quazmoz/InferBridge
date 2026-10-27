# Native WinUI ↔ web capability inventory

Source of truth: live `dev` code, not the older release notes. Review this inventory
whenever native UI, FastAPI contracts or release packaging change. "Full native"
describes comparable user functionality, **not** proof that a signed installer has
passed Windows hardware certification.

| Domain | Native status | Evidence and intentional limitations |
|---|---|---|
| Chat, model switching and streaming | Full native for the shared subset | `MainWindow.xaml.cs`, `MainWindow.ChatRendering.cs`, `MainWindow.ChatTools.cs`, `BackendClient.cs`: SSE, stop waiting, message-level copy, per-chat system prompt; non-stop SSE finishes cannot enter saved history |
| Chat Markdown rendering | Same safe subset on both sides | `ChatMarkdown.cs` mirrors `renderMarkdown` in `web/index.html`: ATX headings (h1–h6), fenced code with copy-code, thematic breaks, flat bullet/ordered lists, block quotes, inline code, strong and emphasis. Links, images and raw HTML stay literal text. Neither native nor web implements full CommonMark |
| Vision image attachments | Native, capability-gated | `MainWindow.ChatTools.cs`: attachments are only offered when the engine reports `supports_vision` for the selected model (`GetModelSupportsVisionAsync`) and are sent as OpenAI `image_url` content parts; actual image understanding depends on the backend and model |
| Conversation management | Partial native | `NativeConversationStore.cs`: opt-in local search, rename, delete, restore, Markdown export and `inferbridge-conversation` JSON export/import (4 MiB limit, same as web). JSON files are a manual exchange format; there is no automatic native↔browser history sync. Pin, archive and duplicate remain web-only |
| Catalog model discovery and download | Full native | `MainWindow.xaml.cs`, `BackendClient.cs`: curated catalog, preparation progress, retry |
| Verified Model Library import/export/adopt | Web-only | native lists and downloads catalog models but cannot import, export or adopt a Verified Model Library bundle |
| Hugging Face search and custom downloads | Full native for supported options | `MainWindow.HuggingFace.cs`, `HuggingFaceClient.cs`: account/search/token, conversion queue and conversion options (backend, weight format, group size, ratio, symmetric quantization, device, context/output tokens, load-after). `trust_remote_code` is deliberately always `false` from native; models that need remote code must use the web UI |
| Model conversion and recovery | Partial native | `MainWindow.ModelPreparation.cs`, `ModelPreparationClient.cs`: guarded recovery and cancellation |
| Inference and generation configuration | Partial native | `MainWindow.GenerationSettings.cs`, `ContextConfigurationClient.cs`: request-only tokens/temperature/top-p plus persistent per-model context via backend; the web chat settings now also send `top_p` (persisted locally). No NPU KV precision switch or arbitrary runtime options; Windows hardware validation pending |
| Hardware/device inspection and chat target selection | Native-only selector, hardware verification pending | `GetDevicesAsync` exposes detected CPU/GPU/NPU and backend-suggested AUTO/MULTI/HETERO combinations. Chat offers an explicit load/switch request, with requested target distinct from the engine-reported active device; real multi-device behavior is experimental and unverified. The web UI has no equivalent selector |
| Benchmark run history and comparable results | Full native for history | `BenchmarkHistoryClient.cs`, `MainWindow.Benchmark.cs`: authenticated bounded history, per-row measurement and strict compatibility; actual Windows execution awaits CI |
| Native Benchmark Lab matrix and progress | Native-only | `MainWindow.Benchmark.cs`, `BenchmarkLabClient.cs`: multiple prepared generation models, discovered direct devices and AUTO routing, Quick/Standard/Thorough presets, bounded eight-combination runs and operation-correlated engine-event progress; missing/truncated events remain indeterminate and no server-side cancellation API exists. No web equivalent |
| System Doctor and model health | Full native for existing remediation | native Doctor screen, `RuntimeHealthClient.cs`, `MainWindow.Maintenance.cs`: core checks, privacy-safe report, and model-health remediation through the existing protected `/v1/runtime-health/action` and `/v1/runtime-health/batch` endpoints with explicit confirmation |
| Storage/cache inventory and cleanup | Full native | `GetStorageInventoryAsync` and guarded cleanup; engine rechecks permissions |
| Hugging Face authentication | Full native | `HuggingFaceClient.cs`: backend-controlled credential storage, verification and removal (as permitted) |
| Local Connection Hub | Native for details and self-test | `ConnectionHubClient.cs`, `MainWindow.ConnectionHub.cs`: verified dynamic loopback port, authentication and LAN labels, usable loaded models, explicit model selection, secret-free Python/curl examples and individually reported engine-owned synthetic self-tests |
| Network and API listener configuration | Full native via the engine | `ConnectionHubClient.cs`, `MainWindow.Maintenance.cs`: reads and changes listener/LAN, CORS and API-key settings through the same protected `/internal/desktop-network` endpoint the browser uses (verified origin, `X-OV-LLM-UI` header, confirmation for risky choices such as wildcard CORS). Firewall rules stay outside the app |
| Engine restart | Full native | `POST /v1/desktop/operations/restart-server` with confirmation (tray controller restarts the engine; refused while a benchmark or model preparation runs); cached network state is discarded afterwards |
| Diagnostics and support export | Full native | `GetDesktopOperationsAsync`, `ExportDiagnosticsAsync`: server-owned ZIP generation, explicit confirmation |
| Model preparation recovery | Full native for supported actions | `ModelPreparationClient.cs`: backend-owned action restrictions and recovery IDs |
| Update settings and checks | Full native | `ReleaseUpdateClient.cs`: reads/saves `/desktop/release/settings`, checks and skips releases; release links are restricted to official release URLs (`IsOfficialReleaseUrl`). Installing an update is still handed to the official installer/package |
| App settings and startup | Partial native | native Settings, `app/startup_registration.py`: local runtime/UX controls; web controls some advanced preferences |
| Embedding tools | Web-only / backend-dependent | backend determines actual model capabilities |

### Remaining intentional gaps

- Web-only: Verified Model Library import/export/adopt; conversation pin, archive and duplicate; `trust_remote_code` Hugging Face conversions.
- Native-only: chat device routing selector and Benchmark Lab.
- Neither side: full CommonMark, automatic native↔browser conversation sync.

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
and an image attachment against a model that reports `supports_vision`. These
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
