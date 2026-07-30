# InferBridge identity inventory

This inventory records the repository-wide identity review performed before the public product rename from **OpenVINO Windows LLM** to **InferBridge**. It distinguishes product branding from the OpenVINO GenAI runtime and from identifiers that must remain compatible.

## Search scope

The review covered tracked source, hidden configuration, GitHub workflows, Python packaging, PyInstaller, Inno Setup, release and signing scripts, updater trust rules, browser assets, tests, current documentation, historical release notes, and retained certification evidence.

The reviewed terms and identifier families were:

- `OpenVINO Windows LLM`
- `OpenVINO LLM`
- `OpenVINOWindowsLLM`
- `OpenVINO-Windows-LLM`
- `openvino-windows-llm`
- `ovllm`
- `OV_LLM`
- repository, release, executable, installer, shortcut, data-directory, registry, user-agent, manifest, diagnostics, icon, and asset identifiers

## 1. Public display branding changed to InferBridge

Current public-facing identity was migrated in these areas:

- `app/brand.py`: canonical product, executable, repository, description, tagline, and artifact constants
- `app/__init__.py`: package description
- `app/branding_ui.py`: browser title, metadata description, header name, tagline, favicon, and application icon injection
- `app/desktop_launcher.py`, `app/desktop_shell.py`, `app/tray_support.py`, `app/tray_menu.py`: desktop dialogs, tray title, tooltip, About presentation, and launcher help
- `app/diagnostics.py`: current diagnostic archive and product presentation
- `web/index.html`, `web/app-icon.svg`: browser fallback markup and accessible icon title
- `packaging/openvino_windows_llm.spec`: emitted executable and distribution directory
- `packaging/installer.iss`: installer, Add or Remove Programs, shortcut, launch, and default installation names
- `scripts/generate_brand_assets.py`, `scripts/build_release.ps1`, `scripts/publish_release.ps1`, `scripts/release_manifest.py`, `scripts/release_scan.py`, `scripts/verify_release_signing.py`, `scripts/verify_release_provenance.py`, and `scripts/smoke_test_packaged.ps1`: generated product metadata and canonical release artifacts
- `README.md`, `QUICKSTART.md`, `CONTRIBUTING.md`, `OPENWEBUI.md`, and current installation, portable, tray, diagnostics, troubleshooting, update, release, architecture, and rollback documentation
- `docs/releases/0.7.0.md`: current rename release notes

## 2. Technical OpenVINO terminology retained

These are runtime or ecosystem terms, not legacy product branding, and remain unchanged:

- `openvino` and `openvino-genai` package and distribution names
- OpenVINO GenAI imports, runtime adapters, model conversion, compiled-model behavior, and dependency metadata
- OpenVINO IR and cache terminology, including `cache/openvino`
- Intel CPU, GPU, NPU, AUTO, MULTI, and HETERO device terminology
- OpenVINO version, driver, model, precision, compilation, benchmark, and certification fields
- OpenVINO-compatible model manifests and retained certification evidence
- “Powered by OpenVINO GenAI” and equivalent accurate runtime descriptions

No global replacement of `OpenVINO` was performed.

## 3. Compatibility-sensitive legacy identity retained

The following identifiers deliberately remain accepted or preserved:

- Inno Setup `AppId`: `{F94A3938-C943-4E6D-B482-852D4AAE06F8}`
- legacy executable: `OpenVINOWindowsLLM.exe`
- legacy data directory: `%LOCALAPPDATA%\OpenVINOWindowsLLM`
- legacy Start with Windows value: `OpenVINOWindowsLLM`
- legacy repository: `Quazmoz/openvino-windows-llm`
- legacy release artifact prefixes: `OpenVINO-Windows-LLM` and `OpenVINOWindowsLLM`
- Python distribution name: `openvino-windows-llm`
- console commands: `ov-llm` and `ov-llm-desktop`
- all existing `OV_LLM_*` environment variables
- existing OpenAI-compatible endpoints, JSON schemas, API-key behavior, streaming behavior, tool-call behavior, model IDs, stored data schema, host binding, and mock contracts

New aliases `inferbridge` and `inferbridge-desktop` call the same implementations as the legacy commands.

## 4. Historical records preserved accurately

The following remain historical records and are not rewritten to imply that older releases shipped as InferBridge:

- `docs/releases/0.4.0.md` through the existing `0.6.x` release notes
- retained Windows certification and compatibility evidence under `docs/certification/`
- Git history, tags, releases, issues, and existing release asset names
- README links to already-published releases, which retain their original repository location and filenames

A migration note may explain the subsequent rename without changing the historical product name of an old release.

## 5. Generated or transient content not manually migrated

The following are generated, local, or transient and are rebuilt from canonical sources rather than edited as identity sources:

- `build/`, `dist/`, `artifacts/`, virtual environments, caches, logs, and temporary smoke-test directories
- generated PyInstaller version metadata and build metadata
- generated ICO and release inventory files
- locally downloaded or converted models and compiled caches
- one-time migration and validation workflows, which remove themselves after successful validation

## Repository transition rule

The product can be released as InferBridge while the repository still uses `Quazmoz/openvino-windows-llm`. Release generation defaults to that live repository before the rename and accepts `Quazmoz/InferBridge` after the rename. The updater trusts only those exact owner/repository combinations and their exact GitHub release paths.
