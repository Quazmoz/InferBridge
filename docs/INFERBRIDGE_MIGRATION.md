# InferBridge migration and repository-rename plan

## Product positioning

**InferBridge** is local AI for Intel hardware: a Windows-first local AI server with an OpenAI-compatible API, powered by OpenVINO GenAI.

OpenVINO remains the inference runtime and dependency. InferBridge does not replace or own OpenVINO and does not claim affiliation with Intel.

## Compatibility strategy

The safest repository transition is to publish a compatibility-aware InferBridge release from `Quazmoz/openvino-windows-llm` before changing the GitHub slug. That client accepts both old and new repository release locations, manifest names, and artifact aliases. Older installations that predate this compatibility support may require one manual installer upgrade if GitHub's release API or embedded asset URLs do not redirect reliably after the rename.

The repository has not been renamed by this code migration. The final manual action is:

`Repository Settings > General > Repository name > InferBridge > Rename`

Never create a replacement repository at `Quazmoz/openvino-windows-llm`; GitHub needs the old path to remain available as a redirect.

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

After the manual rename, verify clone, fetch, push, web redirects, Git redirects, issues, stars, releases, tags, branches, history, release API responses, manifest downloads, checksums, and release asset redirects. Update the local clone origin to `https://github.com/Quazmoz/InferBridge.git` only after the rename succeeds.
