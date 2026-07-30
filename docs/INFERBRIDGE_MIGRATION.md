# InferBridge migration and repository-rename plan

## Product positioning

**InferBridge** is local AI for Intel hardware: a Windows-first local AI server with an OpenAI-compatible API, powered by OpenVINO GenAI.

OpenVINO remains the inference runtime and dependency. InferBridge does not replace or own OpenVINO and does not claim affiliation with Intel.

## Repository rename status

The repository was renamed to `Quazmoz/InferBridge` on July 30, 2026. GitHub retained the repository identity, history, branches, issues, releases, tags, and stars.

No compatibility-aware public release was published before the slug changed. Older installations that predate InferBridge-aware update discovery may require one manual installer upgrade if GitHub's legacy releases API or embedded release URLs do not redirect reliably. InferBridge 0.7.0 and later accept both canonical and legacy repository release locations, manifest names, and artifact prefixes.

Do not create a replacement repository at `Quazmoz/openvino-windows-llm`; the old path must remain available as GitHub's redirect.

## Installer behavior

- Preserve `AppId={{F94A3938-C943-4E6D-B482-852D4AAE06F8}` exactly.
- New installs default to `%LOCALAPPDATA%\Programs\InferBridge`.
- Existing installs may retain their previous install directory through the preserved `AppId` and `UsePreviousAppDir=yes`.
- Upgrade cleanup recognizes `InferBridge.exe` and `OpenVINOWindowsLLM.exe` and removes legacy executable and shortcut remnants.
- Uninstall preserves mutable data by default. Explicit removal deletes only the exact InferBridge and legacy application-data directories.

## Data-directory selection

1. `OV_LLM_DATA_DIR` remains the highest-priority override.
2. Portable mode continues using `<portable root>\data`.
3. Installed mode uses `%LOCALAPPDATA%\InferBridge` when that directory already exists.
4. If the new directory does not exist and `%LOCALAPPDATA%\OpenVINOWindowsLLM` exists, the legacy directory remains active.
5. A clean install uses `%LOCALAPPDATA%\InferBridge`.
6. No automatic multi-gigabyte move or merge occurs.
7. If both directories contain data, InferBridge is selected and a privacy-safe warning is emitted.

## Start with Windows

InferBridge uses the registry value name `InferBridge`. A recognized enabled `OpenVINOWindowsLLM` value that points to the previous executable is migrated to the new value and command. Disabled users remain disabled, portable mode remains ineligible, and unrelated registry values are untouched.

## Post-rename verification

The GitHub connector confirms the canonical repository is `Quazmoz/InferBridge` and the legacy repository identifier resolves to the renamed repository. Before publishing 0.7.0, verify clone, fetch, push, old web and Git redirects, the legacy REST releases endpoint, manifest and checksum downloads, and release-asset redirects from a real network environment.

## Release repository selection

Release manifests now default to `Quazmoz/InferBridge`. GitHub Actions also resolves the canonical repository through `GITHUB_REPOSITORY`. `OV_LLM_RELEASE_REPOSITORY` remains a restricted transition override that accepts only `Quazmoz/InferBridge` or `Quazmoz/openvino-windows-llm` for compatibility testing and historical release verification.
