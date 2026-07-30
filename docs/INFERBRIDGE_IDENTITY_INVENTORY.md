# InferBridge identity inventory

This inventory records the repository-wide identity review performed for the public product rename from **OpenVINO Windows LLM** to **InferBridge**. It distinguishes current product branding from the OpenVINO GenAI runtime and from identifiers that remain for backward compatibility or historical accuracy.

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

## 1. Public display branding uses InferBridge

Current public-facing identity is canonicalized in these areas:

- `app/brand.py`: product, executable, repository, description, tagline, and artifact constants
- `app/__init__.py`: package description
- `app/branding_ui.py`: browser title, metadata description, header name, tagline, favicon, and application icon injection
- `app/desktop_launcher.py`, `app/desktop_shell.py`, `app/tray_support.py`, and `app/tray_menu.py`: desktop dialogs, tray title, tooltip, About presentation, and launcher help
- `app/diagnostics.py`: current diagnostic archive and product presentation
- `web/index.html` and `web/app-icon.svg`: browser fallback markup and accessible icon title
- `packaging/openvino_windows_llm.spec`: emitted InferBridge executable and distribution directory
- `packaging/installer.iss`: installer, Add or Remove Programs, shortcuts, launch entry, and default installation names
- release, signing, manifest, checksum, and packaged-smoke scripts: generated InferBridge product metadata and release artifacts
- `README.md`, `QUICKSTART.md`, `CONTRIBUTING.md`, `OPENWEBUI.md`, and current installation, portable, tray, diagnostics, troubleshooting, update, release, architecture, rollback, Linux, API, and model-library documentation
- `.claude/skills/use_memoryops.md` and `.gemini/skills/use_memoryops.md`: canonical repository and agent metadata
- `docs/releases/0.7.0.md`: rename release notes and completed repository transition

## 2. Technical OpenVINO terminology remains

These are runtime or ecosystem terms, not legacy product branding:

- `openvino` and `openvino-genai` package and distribution names
- OpenVINO GenAI imports, runtime adapters, model conversion, compiled-model behavior, and dependency metadata
- OpenVINO IR and cache terminology, including `cache/openvino`
- Intel CPU, GPU, NPU, AUTO, MULTI, and HETERO device terminology
- OpenVINO version, driver, model, precision, compilation, benchmark, and certification fields
- OpenVINO-compatible model manifests and retained certification evidence
- “Powered by OpenVINO GenAI” and equivalent accurate runtime descriptions

No global replacement of `OpenVINO` is appropriate.

## 3. Compatibility-sensitive legacy identity remains

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
- internal `.ovllm-*` metadata filenames and browser storage keys whose renaming would break existing installations
- existing OpenAI-compatible endpoints, JSON schemas, API-key behavior, streaming behavior, tool-call behavior, model IDs, stored data schema, host binding, and mock contracts

New aliases `inferbridge` and `inferbridge-desktop` call the same implementations as the legacy commands.

## 4. Historical records remain accurate

The following are historical records and must not be rewritten to imply that older releases shipped as InferBridge:

- release notes for versions before 0.7.0
- retained Windows certification and compatibility evidence under `docs/certification/`
- Git history, tags, releases, issues, and existing release asset names
- README links to already-published releases whose asset names use the former product identity

A migration note may explain the subsequent rename without changing the historical product name of an older release.

## 5. Generated or transient content is rebuilt

The following are generated, local, or transient and are rebuilt from canonical sources rather than treated as identity sources:

- `build/`, `dist/`, `artifacts/`, virtual environments, caches, logs, and temporary smoke-test directories
- generated PyInstaller version metadata and build metadata
- generated ICO and release inventory files
- locally downloaded or converted models and compiled caches
- one-time migration and validation workflows

## Repository transition rule

The canonical repository is `Quazmoz/InferBridge`. Release generation, documentation, clone instructions, support links, and new manifests must use that repository by default.

The old `Quazmoz/openvino-windows-llm` path is retained only as a GitHub redirect and as a restricted compatibility source for previously published releases and older installations. Do not create a replacement repository at the old path because doing so would consume the redirect and break migration behavior.
