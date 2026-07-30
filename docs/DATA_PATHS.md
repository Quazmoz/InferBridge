# Desktop data paths

Installed application files may be read-only. Desktop mode therefore separates packaged resources from user-writable data.

## Installed mode

The default data root is:

```text
%LOCALAPPDATA%\InferBridge
```

Subdirectories:

| Directory | Purpose |
|---|---|
| `config` | Writable model catalog and future desktop configuration |
| `logs` | Rotating launcher and server logs |
| `models` | Converted OpenVINO models |
| `cache\huggingface` | Hugging Face download and conversion cache |
| `cache\openvino` | OpenVINO compiled-model cache |
| `benchmarks` | Local benchmark results |
| `diagnostics` | User-requested diagnostics and quarantined incomplete output |
| `onboarding` | Versioned first-run state |

Normal upgrades and uninstall preserve this directory. The uninstaller asks before removing it and defaults to preservation.

## Portable mode

A portable build contains `portable.flag`. Mutable state is stored under:

```text
<portable folder>\data
```

Move the entire portable directory to move the application and its data. Ensure the target volume is writable and has enough free space for model downloads and conversion staging.

## Overrides and development compatibility

`OV_LLM_DATA_DIR` overrides the desktop data root. Existing `OV_LLM_MODELS_FILE`, `OV_LLM_MODELS_DIR`, `OV_LLM_CACHE_DIR`, and `OV_LLM_BENCHMARK_RESULTS` overrides remain supported.

Repository-development mode retains the existing repository-relative paths unless `OV_LLM_DESKTOP=1` is set.

The writable catalog is initialized from the bundled catalog. Existing entries are retained verbatim during upgrades. New bundled entries are appended with paths under the writable model directory. Existing models are never silently moved or deleted.
