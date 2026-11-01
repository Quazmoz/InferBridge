# First-run setup

The packaged desktop flow is:

**Download → Install → Choose recommended model → Chat**

The launcher starts the existing FastAPI server on `127.0.0.1`, selects an available local port, waits for readiness, and opens the existing browser UI. The first-run wizard appears only when the versioned onboarding record is incomplete or the user chooses **First-run setup** again.

## Wizard stages

1. **Welcome** explains local execution, model disk requirements, Intel OpenVINO devices, localhost safety, documentation, and the application version.
2. **System scan** uses the existing hardware advisor and OpenVINO device discovery. Unknown values remain unknown rather than being treated as failures.
3. **NPU readiness** distinguishes OpenVINO-visible NPU support, likely hardware with an unavailable plugin, no detected NPU, unavailable driver information, unsupported platforms, and mock mode.
4. **Recommended model** ranks compatible catalog models conservatively. It considers blocking preflight findings, RAM and disk headroom, OpenVINO-visible devices, model size, first-load cost, and benchmark evidence.
5. **Prepare model** uses the existing lifecycle locks and progress data. Download, conversion, validation, compilation, load, and benchmark stages are shown separately. Indeterminate stages do not display invented percentages.
6. **Benchmark** reports the actual device returned by the runtime, load time, time to first token, throughput, completion token count, and whether the result is mock or measured.
7. **Ready** shows the actual selected port, OpenAI-compatible base URL, model ID, actual device, API-key state, health URL, and copyable OpenAI Python, environment, Open WebUI, and n8n settings.

## Completion and reruns

Onboarding is marked complete only after the model loads and the short benchmark succeeds. Restarting the application reloads the verified model and skips the wizard. Existing models, benchmarks, and settings are retained when setup is restarted.

If the hardware fingerprint changes because devices, drivers, memory, or OpenVINO versions changed, the application recommends a new scan without deleting state or forcing every user through setup.

## Privacy and safety

The onboarding record contains operational values only. It does not store prompts or chat content. Connection examples never reveal a configured API key and use a placeholder until the user deliberately supplies the secret.

## Model download, conversion, and first load

For public Hugging Face models, an access token is normally unnecessary. A token is
needed for gated/private repositories when your account has permission, but it is
not a remedy for OpenVINO conversion or device compilation errors. Never paste a
token into a support comment or diagnostics archive.

Model preparation has distinct stages: **download** fetches source weights,
**conversion** exports validated OpenVINO IR, and **load** initializes the model on
the selected CPU/GPU/NPU. Large downloads and conversions can take significant time
and may show indeterminate progress; wait for the model to reach **Loaded** before
concluding it is ready for chat. A past failed attempt should not be mistaken for
a failure of a newer retry.

If preparation fails, open the model's **Recovery** details to see the failed
stage and sanitized error. **Resume preparation** reuses eligible cached source
files; it does not re-download everything. Retry a small public FP16 model on CPU
to distinguish a general converter problem from NPU compilation or memory pressure.
If this also fails, use **Tray → Export Diagnostics**, review the ZIP for private
data, and report the model ID, build/version, selected device, and actual error.
