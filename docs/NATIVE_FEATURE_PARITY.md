# Native WinUI ↔ web capability inventory

Source of truth: live `dev` code, not the older release notes. Review this inventory
whenever native UI, FastAPI contracts or release packaging change. "Full native"
describes comparable user functionality, **not** proof that a signed installer has
passed Windows hardware certification.

| Domain | Native status | Evidence and intentional limitations |
|---|---|---|
| Chat, model switching and streaming | Partial native | `MainWindow.xaml.cs`, `MainWindow.ChatRendering.cs`, `BackendClient.cs`: SSE, stop waiting, message-level copy, native headings/emphasis/fenced code with copy-code controls; non-stop SSE finishes cannot enter saved history. Full CommonMark, vision attachments and browser-history sync remain unavailable. |
| Conversation management | Partial native | `NativeConversationStore.cs`: opt-in local search, rename, delete, restore; no browser-history sync |
| Catalog model discovery and download | Full native | `MainWindow.xaml.cs`, `BackendClient.cs`: curated catalog, preparation progress, retry |
| Hugging Face search and custom downloads | Partial native | `MainWindow.HuggingFace.cs`, `HuggingFaceClient.cs`: account/search/token, conversion queue; advanced conversion tuning remains web-only |
| Model conversion and recovery | Partial native | `MainWindow.ModelPreparation.cs`, `ModelPreparationClient.cs`: guarded recovery and cancellation, not all web conversion options |
| Inference and generation configuration | Partial native | `MainWindow.GenerationSettings.cs` and `ContextConfigurationClient.cs`: request-only tokens/temperature/top-p plus persistent per-model context via backend; no NPU KV precision switch or arbitrary runtime options; Windows hardware validation pending |
| Hardware/device inspection and chat target selection | Native selector, hardware verification pending | `GetDevicesAsync` exposes detected CPU/GPU/NPU and backend-suggested AUTO/MULTI/HETERO combinations. Chat offers an explicit load/switch request, with requested target distinct from the engine-reported active device; real multi-device behavior is experimental and unverified |
| Benchmark run history and comparable results | Full native for history | `BenchmarkHistoryClient.cs`, `MainWindow.Benchmark.cs`: authenticated bounded history, per-row measurement and strict compatibility; actual Windows execution awaits CI |
| Native Benchmark Lab matrix and progress | Native configuration and execution | `MainWindow.Benchmark.cs`, `BenchmarkLabClient.cs`: multiple prepared generation models, discovered direct devices and AUTO routing, Quick/Standard/Thorough presets, bounded eight-combination runs and operation-correlated engine-event progress; missing/truncated events remain indeterminate and no server-side cancellation API exists |
| System Doctor | Partial native | `RunSystemDoctorAsync`, native Doctor screen: core checks and privacy-safe report; some advanced remediation remains web-only |
| Storage/cache inventory and cleanup | Full native | `GetStorageInventoryAsync` and guarded cleanup; engine rechecks permissions |
| Hugging Face authentication | Full native | `HuggingFaceClient.cs`: backend-controlled credential storage, verification and removal (as permitted) |
| Local Connection Hub | Native for read-only details and self-test | `ConnectionHubClient.cs`, `MainWindow.ConnectionHub.cs`: verified dynamic loopback port, authentication and LAN labels, usable loaded models, explicit model selection, secret-free Python/curl examples and individually reported engine-owned synthetic self-tests; listener mutation remains web-only |
| Network and API listener configuration | Web-only mutation | native Hub reports listener/LAN state; it cannot change bindings, firewall rules or credentials |
| Diagnostics and support export | Full native | `GetDesktopOperationsAsync`, `ExportDiagnosticsAsync`: server-owned ZIP generation, explicit confirmation |
| Model preparation recovery | Full native for supported actions | `ModelPreparationClient.cs`: backend-owned action restrictions and recovery IDs |
| App settings and startup | Partial native | native Settings, `app/startup_registration.py`: local runtime/UX controls; web controls some advanced preferences |
| Full CommonMark, vision and embedding tools | Web-only / backend-dependent | native renders a safe Markdown subset without HTML, script or remote-resource execution; backend determines actual model capabilities |

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

After that, complete CommonMark parity and optional vision-image attachments
only against a confirmed backend/model capability contract.

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
