# InferBridge

**Local AI for Intel hardware. Powered by OpenVINO GenAI.**

InferBridge turns Intel Windows PCs into local AI workstations. It provides a Windows-first, OpenAI-compatible server with streaming chat, local vision chat, text embeddings, model lifecycle and recovery workflows, secure Hugging Face access, context-budget visibility, CPU/GPU/NPU device targeting, System Doctor diagnostics, hardware benchmarking, a built-in browser UI, and a deterministic mock engine.

OpenVINO GenAI remains the inference runtime. InferBridge does not replace OpenVINO, claim ownership of it, or imply affiliation with Intel. The project does not require Docker, cloud inference, Electron, or a Node frontend toolchain.

## Video walkthrough

[Watch the InferBridge walkthrough](https://youtu.be/rya6rJhkQrw)

## Project and release status

**Current stable release: `0.9.6`.** See [0.9.6](docs/releases/0.9.6.md) for the full record. It promotes the 0.9.6 beta architecture, installer hardening, security boundaries, lifecycle reliability, support workflows, and privacy-safe diagnostics to the stable channel.

- [Windows installer](https://github.com/Quazmoz/InferBridge/releases/download/v0.9.6/InferBridge-0.9.6-windows-x64-installer.exe)
- [Portable ZIP](https://github.com/Quazmoz/InferBridge/releases/download/v0.9.6/InferBridge-0.9.6-windows-x64-portable.zip)
- [SHA-256 checksums](https://github.com/Quazmoz/InferBridge/releases/download/v0.9.6/InferBridge-0.9.6-checksums.txt)

There is no pre-release newer than the current stable release. Versions 0.9.0 through 0.9.3 were published only as betas and are superseded by 0.9.6. Beta builds ship ahead of the next stable release and are marked pre-release on GitHub.

Releases from `v0.7.0` onward use the canonical `Quazmoz/InferBridge` repository and `InferBridge-<version>` artifact names. Releases through `v0.6.3` were published before the rename, and their repository links and artifact filenames intentionally retain the former product identity.

> **Validation scope:** Mock-mode tests validate API, UI, packaging, lifecycle, and state contracts. Real CPU, GPU, and NPU claims require the Windows certification harness on suitable hardware with mock mode disabled. An unsigned development installer is not a signed production release.

The retained 0.6.1 hardware evidence comes from one Intel Core Ultra 9 185H laptop running Windows 11 build 26200 and OpenVINO `2026.2.1`. It is not a claim that every Intel system, model, precision, driver, or OpenVINO release will behave the same way, and no later release has been recertified on hardware.

> **Signing status:** No published InferBridge installer or portable ZIP is Authenticode-signed. Windows reports an unknown publisher and SmartScreen may warn on first launch. Verify downloads against the published SHA-256 checksums.

## Download, install, choose a model, and chat

1. Download a versioned installer or portable ZIP.
2. Run `InferBridge.exe` from the installed shortcut or extracted directory.
3. Review the hardware and OpenVINO system scan.
4. Confirm the available Intel CPU, GPU, or NPU target.
5. Accept the conservative model recommendation or choose another compatible model.
6. Confirm model license, disk, and warning information.
7. Follow the download, conversion, validation, compilation, loading, and benchmark stages.
8. Continue to chat or copy the OpenAI-compatible endpoint for another client.

The desktop launcher binds to `127.0.0.1` by default, selects an available port, waits for liveness and readiness, opens the browser, and prevents duplicate instances. Packaged **Network / API access** settings can deliberately enable authenticated LAN access while internal health checks and desktop control remain on loopback. See [LAN and home-lab access](docs/LAN_ACCESS.md). Model weights are downloaded separately and are not included in the base installer.

The first model setup can take significant time. NPU support depends on the actual Intel platform, driver, model, precision, and OpenVINO release. A requested target is not proof of execution. InferBridge reports the actual device from successful measured generation when the runtime exposes that evidence.

## Installation options

### Windows installer

The Inno Setup package installs per-user under:

```text
%LOCALAPPDATA%\Programs\InferBridge
```

It creates a Start Menu shortcut, can create an optional desktop shortcut, and preserves mutable data by default during upgrades and uninstall.

See the [Windows installer guide](docs/INSTALLER.md).

### Portable ZIP

Extract the complete ZIP to a writable directory and run `InferBridge.exe`. Portable mode stores mutable data under a sibling `data` directory and does not create registry or Start Menu entries.

See the [portable package guide](docs/PORTABLE.md).

### Source setup on Windows

```powershell
git clone https://github.com/Quazmoz/InferBridge.git
cd InferBridge
.\setup.bat
.\start_server.bat --mock
```

Start a real converted model on CPU:

```powershell
.\start_server.bat `
  --model tinyllama-1.1b-chat-fp16 `
  --device CPU `
  --auto-convert
```

Use `GPU`, `NPU`, or `AUTO` only when OpenVINO reports the target:

```powershell
.\start_server.bat --check-devices
```

See [QUICKSTART.md](QUICKSTART.md) for the complete setup flow.

## Visual preview

### Main chat interface

![InferBridge chat interface](screenshots/chat_preview.png)

### System Doctor hardware diagnostics

![InferBridge System Doctor](screenshots/system_doctor.png)

### Settings and system information

![InferBridge settings](screenshots/settings_preview.png)

### First-run model flow

![InferBridge empty state](screenshots/empty_state.png)

### Light theme

![InferBridge light theme](screenshots/light_theme.png)

### Responsive layout

![InferBridge responsive preview](screenshots/responsive_preview.png)

## Documentation

### Start and operate

- [Quick start](QUICKSTART.md)
- [First-run guide](docs/FIRST_RUN.md)
- [Windows installer](docs/INSTALLER.md)
- [Portable package](docs/PORTABLE.md)
- [Windows setup](docs/WINDOWS.md)
- [Experimental Linux overview](docs/LINUX.md)
- [Experimental Ubuntu setup](docs/UBUNTU.md)
- [Experimental Fedora setup](docs/FEDORA.md)

### Architecture and APIs

- [Desktop architecture](docs/DESKTOP_ARCHITECTURE.md)
- [Browser UI architecture](docs/BROWSER_UI_ARCHITECTURE.md)
- [Desktop onboarding API](docs/DESKTOP_API.md)
- [API contract](docs/API_CONTRACT.md)
- [Local vision chat](docs/VISION.md)
- [Open WebUI guide](OPENWEBUI.md)
- [Open WebUI and n8n integrations](docs/INTEGRATIONS.md)
- [LAN and home-lab access](docs/LAN_ACCESS.md)
- [Device support](docs/DEVICE_SUPPORT.md)
- [Verified Model Library](docs/MODEL_LIBRARY.md)
- [Model preparation cancellation](docs/model-cancellation.md)
- [Model preparation recovery](docs/MODEL_RECOVERY.md)
- [Context budget visibility](docs/CONTEXT_BUDGET.md)

### Data, diagnostics, and support

- [Mutable data paths](docs/DATA_PATHS.md)
- [Privacy-safe diagnostics](docs/DIAGNOSTICS.md)
- [Desktop troubleshooting](docs/TROUBLESHOOTING_DESKTOP.md)
- [Known issues](docs/KNOWN_ISSUES.md)
- [Compatibility matrix](docs/COMPATIBILITY_MATRIX.md)
- [Windows hardware certification](docs/WINDOWS_CERTIFICATION.md)

### Packaging and release

- [Packaging and release overview](docs/PACKAGING_RELEASE.md)
- [Production release process](docs/RELEASE_PROCESS.md)
- [Code signing](docs/CODE_SIGNING.md)
- [Versioning](docs/VERSIONING.md)
- [Update policy](docs/UPDATE_POLICY.md)
- [Upgrade and rollback](docs/UPGRADE_ROLLBACK.md)
- [Third-party licenses](docs/THIRD_PARTY_LICENSES.md)

### Rename and compatibility

- [InferBridge migration and repository rename](docs/INFERBRIDGE_MIGRATION.md)
- [InferBridge identity inventory](docs/INFERBRIDGE_IDENTITY_INVENTORY.md)

### Release notes

- [0.9.6](docs/releases/0.9.6.md) — current stable release
- [0.9.5](docs/releases/0.9.5.md)

## Implemented capabilities

### Inference and OpenAI compatibility

- OpenVINO GenAI text generation on `CPU`, `GPU`, `NPU`, `AUTO`, and advanced composite expressions such as `AUTO:NPU,GPU,CPU`, `MULTI:NPU,GPU,CPU`, and `HETERO:NPU,GPU,CPU`
- `POST /v1/chat/completions` with streaming and non-streaming responses
- `POST /v1/responses` with streaming and non-streaming output
- `POST /v1/embeddings` for registered embedding models such as `bge-small-en-v1.5`
- OpenAI-compatible image content parts for local vision-language models
- Stop sequences, seed, temperature, top-p, token limits, tool-call compatibility, and structured-output fields
- Safe-by-default Hugging Face conversion with remote code disabled unless explicitly enabled for a reviewed model

### Model and device operations

- Maintained model catalog plus custom Hugging Face registration
- Verified Model Library with retained official evidence and local benchmark evidence
- System Doctor checks for runtime, devices, routing, memory, disk, model readiness, and NPU state
- Transactional conversion staging, strict artifact validation, bounded backup and rollback, and incomplete-output protection
- Sanitized progress, operation-scoped cancellation, cross-process preparation locks, and serialized lifecycle operations
- Recovery state with resume, failed-stage retry, fresh-download restart, and incomplete-output cleanup actions
- Stage-aware download, conversion, finalization, loading, and compilation watchdogs that distinguish slow progress from a stalled operation
- Secure Hugging Face token storage with Windows DPAPI, gated-model access preflight, and environment variables retained as an advanced fallback
- Optional conversion when loading a missing model
- OpenVINO hardware discovery and NPU readiness checks
- Uninterrupted device switching that keeps the working engine available until replacement handoff
- Local benchmark persistence and conservative target recommendations

### Windows desktop distribution

- Hidden-console launcher with secure loopback default and opt-in authenticated LAN binding
- Per-user single-instance lock with live nonce verification
- Bounded liveness and readiness polling
- Safe port fallback and duplicate-instance prevention
- Startup dialogs, system tray integration, and rotating logs
- Privacy-safe diagnostic ZIP export
- Installed and portable writable-path strategies
- Versioned onboarding state with corruption recovery
- Connection examples for the OpenAI Python SDK, environment variables, Open WebUI, and n8n
- PyInstaller executable, portable ZIP, Inno Setup installer, checksums, release manifests, and Authenticode signing gates

### Browser UI

- Responsive local chat with browser-stored conversation history
- Light and dark themes
- Model selection, device targeting, load, unload, deletion, conversion, and cancellation controls
- Model preparation recovery with reusable-cache status, recommended actions, and sanitized failure details
- Exact tokenizer-backed context-budget preflight with whole-turn omission visibility and output-limit actions
- Secure Hugging Face access settings and gated-model preflight without storing tokens in browser localStorage
- Active operation progress and queue visibility
- System Doctor diagnostics and privacy-safe support report copying
- Hardware Benchmark comparisons with TTFT and tokens-per-second measurements
- Dependency-free Markdown rendering, syntax highlighting, and code copying
- Token, latency, RAM, disk, and request metrics
- Accessible first-run onboarding with visible progress and keyboard navigation

## Data paths and compatibility

Installed mode uses:

```text
%LOCALAPPDATA%\InferBridge
```

Portable mode uses:

```text
<portable directory>\data
```

Configuration, logs, models, Hugging Face cache, OpenVINO compiled cache, benchmarks, diagnostics, and onboarding state are separated. Normal upgrades and uninstall preserve mutable data unless the user explicitly requests removal.

InferBridge 0.7.0 and later recognize the legacy `%LOCALAPPDATA%\OpenVINOWindowsLLM` data directory and do not automatically move or merge multi-gigabyte model data. See [DATA_PATHS.md](docs/DATA_PATHS.md) and [UPGRADE_ROLLBACK.md](docs/UPGRADE_ROLLBACK.md).

Existing environment variables remain supported:

```text
OV_LLM_HOST
OV_LLM_DATA_DIR
OV_LLM_MODELS_FILE
OV_LLM_MODELS_DIR
OV_LLM_CACHE_DIR
OV_LLM_BENCHMARK_RESULTS
OV_LLM_API_KEY
OV_LLM_CORS_ORIGINS
OV_LLM_DEVICE
OV_LLM_MODEL
OV_LLM_MOCK
```

The `OV_LLM_*` prefix, `ov-llm` commands, Python distribution name `openvino-windows-llm`, and internal `.ovllm-*` metadata names are retained for backward compatibility. New command aliases are:

```text
inferbridge
inferbridge-desktop
```

## API overview

```text
GET    /                         Built-in InferBridge UI
GET    /health                   Runtime and lifecycle summary
GET    /health/live              Liveness probe
GET    /health/ready             Readiness probe

GET    /v1/models                     OpenAI-style model list
POST   /v1/chat/completions           Streaming or non-streaming chat
POST   /v1/chat/context-budget        Exact context-window preflight
POST   /v1/responses                  Responses API
POST   /v1/embeddings                 Float or base64 embeddings

POST   /v1/models/register            Register a custom model
POST   /v1/models/convert             Convert a model
POST   /v1/models/load                Load a model
POST   /v1/models/unload              Unload a model
POST   /v1/models/delete              Delete an unloaded model
POST   /v1/models/cancel              Cancel the current preparation operation
GET    /v1/models/recovery/{model_id} Get sanitized recovery details
POST   /v1/models/recovery/action     Apply a scoped recovery action

GET    /v1/huggingface/status         Read masked Hugging Face access status
POST   /v1/huggingface/token          Validate and securely store a token
DELETE /v1/huggingface/token          Remove the securely stored token
POST   /v1/huggingface/test           Test configured Hugging Face access
POST   /v1/huggingface/preflight      Check model access before conversion

GET    /v1/devices                    OpenVINO device discovery
GET    /v1/system/status         Telemetry, lifecycle, metrics, events
GET    /v1/model-library         Curated model library
POST   /v1/model-library/refresh Refresh approved model definitions
POST   /v1/benchmarks/run        Run local hardware benchmarks
GET    /v1/benchmarks            List saved benchmark runs
```

State-changing routes follow the existing API-key policy when `OV_LLM_API_KEY` is configured. Connection examples return placeholders rather than exposing the configured key.

## Configuration example

```powershell
$env:OV_LLM_HOST = "127.0.0.1"
$env:OV_LLM_PORT = "8000"
$env:OV_LLM_DEVICE = "CPU"
$env:OV_LLM_MODEL = "tinyllama-1.1b-chat-fp16"
$env:OV_LLM_AUTO_CONVERT = "1"
$env:OV_LLM_API_KEY = ""
$env:OV_LLM_CORS_ORIGINS = ""
$env:OV_LLM_RATE_LIMIT = "0"
$env:OV_LLM_MOCK = ""
$env:HF_TOKEN = ""
```

Keep the host at `127.0.0.1` unless LAN access is deliberate, authenticated, firewall-restricted, and limited to a trusted private network. InferBridge is not a hardened public internet gateway.

## Device behavior

Simple targets:

```text
CPU
GPU
NPU
AUTO
```

Advanced examples:

```text
AUTO:NPU,GPU,CPU
MULTI:NPU,GPU,CPU
HETERO:NPU,GPU,CPU
```

Composite routing is experimental and does not guarantee faster generation. OpenVINO discovery is the source of truth. Real support claims require the Windows certification harness.

## Build and validate a Windows distribution

```powershell
.\scripts\build_windows_distribution.ps1
.\scripts\smoke_test_packaged.ps1 -DistributionPath .\dist\InferBridge
```

For the production release process, use `scripts\build_release.ps1` and follow [docs/RELEASE_PROCESS.md](docs/RELEASE_PROCESS.md). The release pipeline produces versioned InferBridge artifacts, SHA-256 checksums, release metadata, third-party license inventories, and optional Authenticode signatures.

The packaged mock smoke test validates startup, readiness, UI, onboarding, model preparation, benchmarks, Chat Completions, Responses API, streaming, portable paths, and controlled shutdown. It does not prove real CPU, GPU, NPU, model conversion, installer upgrade, Authenticode trust, or SmartScreen reputation.

## Development and testing

```bash
pip install -e .[dev]
ruff check .
ruff format --check .
pytest
```

External mock contract validation:

```bash
OV_LLM_MOCK=1 python -m app.server --mock --host 127.0.0.1 --port 8123 --model tinyllama-1.1b-chat-fp16
python scripts/validate_api_contract.py --base-url http://127.0.0.1:8123 --profile full --expect-mock --include-embeddings --run-benchmark --exercise-lifecycle
```

Real Windows hardware validation:

```powershell
.\scripts\validate_windows.ps1
```

## Experimental Linux

```bash
git clone https://github.com/Quazmoz/InferBridge.git
cd InferBridge
chmod +x setup.sh start_server.sh setup/*.sh setup/linux/*.sh
./setup.sh --minimal
./start_server.sh --mock
./start_server.sh --model tinyllama-1.1b-chat-fp16 --device CPU
```

Linux GPU and NPU support remains driver-dependent and experimental. InferBridge remains Windows-first.

## Security and privacy

- Loopback is the safe default; packaged LAN binding is explicit and requires API authentication.
- State-changing routes can require an API key.
- Cross-origin browser access is opt-in.
- The browser page allows no inline script: `script-src` is `'self'` plus a per-response nonce, with UI payloads served as same-origin assets.
- Request bodies and image inputs have explicit limits.
- Progress, logs, diagnostics, and user-facing errors sanitize secrets and private paths.
- Diagnostics exclude prompts, chat history, model weights, source images, keys, and certificates.
- Model and conversion lifecycle operations are serialized and bounded.
- Incomplete conversion output is not presented as a valid model.
- Recovery and destructive cleanup refuse symbolic links, Windows junctions, unexpected files, and paths outside configured storage.
- User-supplied Hugging Face tokens use Windows DPAPI storage, are never returned by the API, and are not stored in browser localStorage.
- Certificates, private keys, passwords, tokens, and signing secrets must never enter the repository.

Conversation history remains in browser localStorage and is not persisted by the server.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Use the canonical repository for new clones and links:

```text
https://github.com/Quazmoz/InferBridge
```

## License

MIT. See [LICENSE](LICENSE).
