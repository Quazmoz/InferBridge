# Device Support Notes

InferBridge supports Windows and Linux (x86_64: Ubuntu 22.04/24.04, Debian 12 and Fedora are
tested). Intel GPU and NPU execution on Linux has not yet been verified on physical hardware.

## Target Matrix

| Platform | CPU | GPU | NPU |
|---|---|---|---|
| Windows | Supported | Supported if OpenVINO sees it | Primary target with compatible Intel hardware/drivers |
| Ubuntu / Debian | Supported (CI-verified) | Driver-dependent; not yet hardware-verified | Driver-dependent; not yet hardware-verified |
| Fedora | Supported (CI-verified) | Driver-dependent; not yet hardware-verified | Driver-dependent; not yet hardware-verified |

## Device Discovery

Linux configuration defaults to **CPU**, retaining explicit device selections and
checking them against OpenVINO's inventory before loading. Windows retains its NPU
default. A CPU default is not a claim that every host or model is supported. See
the [Linux qualification table](LINUX.md#qualification) for what CI verifies, and
[Intel GPU and NPU setup](LINUX.md#intel-gpu-and-npu-setup) for drivers. On Linux,
`inferbridge doctor` checks drivers, device permissions and groups and prints fix commands.

Device visibility is ultimately determined by:

```bash
python -m app.server --check-devices
```

On Windows, you can use:

```powershell
.\start_server.bat --check-devices
```

On Linux, you can use:

```bash
inferbridge doctor               # installed app: drivers, permissions, fix commands
./start_server.sh --check-devices  # source checkout
```

If OpenVINO does not list a device, the app cannot use it. `lspci`, Device Manager, or other system tools can provide useful hints, but OpenVINO discovery is the source of truth for this server.

## Device Modes

The server passes device strings to OpenVINO GenAI. Accepted forms:

```text
CPU   GPU   GPU.0   NPU
AUTO                 AUTO:GPU,CPU        AUTO:NPU,GPU,CPU
AUTO:-CPU            AUTO:-GPU           (exclusions only; AUTO's default candidates minus these)
HETERO:GPU,CPU       MULTI:GPU,CPU  (deprecated, migrated to AUTO cumulative)
```

`AUTO:-<device>` (API and Benchmark Lab advanced expressions) excludes devices from AUTO's default candidates. It cannot be mixed with a priority list (`AUTO:GPU,-CPU` is refused) and only AUTO accepts it. Like plain AUTO it never uses the NPU, and excluding every candidate (for example `AUTO:-GPU,-CPU`) is classified unsupported.

### Combined device modes (experimental)

`app/device_modes.py` classifies every combined string per backend before a compile starts and `GET /v1/devices/modes` reports the result. An unsupported combination, an unavailable member or a hint that does not fit the mode is refused with `400` and a plain explanation; `allow_resource_risk: true` loads an *unsupported* combination anyway, at your own risk.

| Mode | Chat LLM | Vision (VLM) | Embedding / rerank | Notes |
|---|---|---|---|---|
| `AUTO`, `AUTO:GPU,CPU` | Supported | Supported | Supported | Picks the first listed device that compiles; one device at a time. Chat gets `PERFORMANCE_HINT=LATENCY` (override with `performance_hint`). |
| `AUTO:NPU,GPU,CPU` | Experimental | Unsupported | Supported | GenAI's NPU pipeline only runs for a direct `NPU` target (see facts). |
| `AUTO:…` + `performance_hint: CUMULATIVE_THROUGHPUT` (and old `MULTI:…`) | Experimental, unsupported with NPU | Experimental, unsupported with NPU | Experimental | Copies the model to every member. InferBridge serves one request per model at a time, so single chats rarely get faster. |
| `HETERO:GPU,CPU` | Experimental | Experimental | Experimental | Splits layers across devices. Chat may add `model_distribution: "pipeline_parallel"` for a model that does not fit GPU memory. |
| `HETERO:` with `NPU` | Unsupported | Unsupported | Unsupported | Not verified. |

#### Combine devices (builder)

The browser (**Combine** beside the Chat device picker) and the native app (**Combine devices…** under the Chat and Model library device pickers) build a combined target without typing an expression:

1. Tick the detected devices to use (two or three of CPU, GPU, NPU) and put them in order with **Move up / Move down**. AUTO tries the first device first; HETERO gives the first device the layers it supports.
2. Choose a mode: **AUTO priority**, **AUTO cumulative throughput** (`performance_hint: CUMULATIVE_THROUGHPUT`), **HETERO** or **HETERO pipeline-parallel** (`model_distribution: pipeline_parallel`). Every mode carries an Experimental badge and the engine's explanation.
3. **Use this target** adds it to the device picker; **Load** (or **Load / switch**) sends the device with its option.

Both clients ask `GET /v1/devices/modes?members=GPU,CPU` (two or three distinct members, `400` otherwise), which returns `builder.options[]` with `id`, `label`, `summary`, `device`, `performance_hint`, `model_distribution`, `experimental` and a `{support, reason}` verdict per backend kind. Modes the engine marks unsupported for the selected model stay visible but disabled with that reason; the clients never decide support themselves. Without `members` the report includes `builder_members` (detected devices) and a default builder for GPU,CPU.

KV cache precision stays **Auto only** for every combined mode (it is sent only to one direct CPU or GPU). The NPU coexistence rule for resident slots uses the resolved members: plain `AUTO` never counts as an NPU load, every expression that names `NPU` does, and an engine that reports its execution devices is judged by where it actually runs.

#### GPU memory suggestion

When the hardware advisor estimates that a chat model needs more memory than the GPU reports, but no more than the GPU plus free RAM, its evaluation carries `device_suggestion: {"device": "HETERO:GPU,CPU", "model_distribution": "pipeline_parallel", "experimental": true, "reason": …}` and an `info` warning (`gpu-memory-split`). The browser and native device summaries show "Suggested: HETERO:GPU,CPU pipeline-parallel (experimental)" and the Combine builder repeats it. It is never selected for you: choose HETERO pipeline-parallel in the builder to use it. No suggestion is made when the GPU does not report its memory.

When a combined mode fails to load, the model card offers **Load on GPU instead** (or **Load on CPU instead**) for the best single member, the error says InferBridge did not switch devices on its own, and the model's `device_fallback` field records the requested device and the offer for diagnostics.

#### Verified facts (2026-11-03)

Checked against the OpenVINO 2026 documentation, the OpenVINO / OpenVINO GenAI `releases/2026/2` sources, and the OpenVINO 2025.4.1 wheel (the newest one installable in the Linux/arm64 build sandbox; 2026.2.1 was not available there). Not yet re-run on Windows with 2026.2.1.

1. **MULTI is deprecated, not removed.** The docs list Multi-Device Execution under *Legacy features* ("new solution: Automatic Device Selection", its behaviour "covered by the CUMULATIVE_THROUGHPUT option"). The `releases/2026/2` build still registers `MULTI` as a device of the AUTO plugin, and on 2025.4.1 `Core().get_versions("MULTI")` reports `openvino_auto_plugin` and `MULTI:CPU` compiles. InferBridge migrates `MULTI:<list>` to `AUTO:<list>` with `PERFORMANCE_HINT=CUMULATIVE_THROUGHPUT` and logs a one-time notice. Sources: <https://docs.openvino.ai/2026/documentation/legacy-features.html>, <https://github.com/openvinotoolkit/openvino/blob/releases/2026/2/src/plugins/auto/CMakeLists.txt>.
2. **Plain AUTO never selects the NPU.** "NPU is currently excluded from the default priority list. To use it for inference, you need to specify it explicitly." Source: <https://docs.openvino.ai/2026/openvino-workflow/running-inference/inference-devices-and-modes/auto-device-selection.html>.
3. **AUTO's CPU startup acceleration does not help LLMs.** "For the models with stateful operations, CPU acceleration feature will also be disabled during model compilation." GenAI LLMs are stateful, so `ENABLE_STARTUP_FALLBACK` is not sent and first tokens do not arrive early on CPU while the GPU compiles. Same source.
4. **HETERO pipeline parallelism exists as a preview.** `MODEL_DISTRIBUTION_POLICY=PIPELINE_PARALLEL` is HETERO's only supported policy and splits a model that does not fit on the first device; the docs recommend at most one of iGPU or CPU, at the end of the list. Source: <https://docs.openvino.ai/2026/openvino-workflow/running-inference/inference-devices-and-modes/hetero-execution.html>.
5. **GenAI and combined devices.** `LLMPipeline` builds its static NPU pipeline only when the device is exactly `"NPU"` (`utils::is_npu_requested`); other strings use the paged-attention backend, which asserts "execution device is expected to be single CPU / single GPU / multi GPUs" and otherwise falls back to the stateful SDPA backend. `VLMPipeline` enables its NPU code path whenever the device string contains `NPU`. So NPU members cannot share a chat model with other devices, and HETERO:GPU,CPU or cumulative CPU+GPU runs chat on the slower stateful backend. Sources: `src/cpp/src/llm/pipeline.cpp`, `src/cpp/src/utils.cpp`, `src/cpp/src/continuous_batching/pipeline_impl.cpp`, `src/cpp/src/visual_language/pipeline.cpp` at <https://github.com/openvinotoolkit/openvino.genai/tree/releases/2026/2>.
6. **Where AUTO landed is not reported by GenAI.** `CompiledModel.get_property(ov::execution_devices)` works on a compiled model (verified on 2025.4.1), and GenAI reads it internally, but the Python `LLMPipeline` binding exposes no compiled model or `get_property` (`src/python/py_llm_pipeline.cpp`). InferBridge reports `execution_devices: null` today and fills it automatically if a future binding exposes the property; mock mode simulates it.

7. **AUTO exclusions.** The 2026 AUTO guide documents `AUTO:-CPU` ("Excluding Devices from Device Candidate List"). In the `releases/2026/2` auto plugin, an exclusion-only list starts from every available device, drops the excluded ones and skips the plugin's block list (`NPU` and non-Intel GPUs), so exclusions never add the NPU. Sources: the AUTO guide above, <https://github.com/openvinotoolkit/openvino/blob/releases/2026/2/src/plugins/auto/src/plugin.cpp> and `plugin_config.cpp` (`device_block_list`).

None of the combined modes has been benchmarked on Intel Core Ultra hardware yet. Use Benchmark Lab's **Compare combined modes** preset to measure them.

## Several Resident Models and the NPU

InferBridge keeps one chat, one embedding and one reranker model loaded at the same time (see [API contract](API_CONTRACT.md#resident-model-slots-local-extension)). Device rules for that:

- **One engine on the Intel NPU.** A load whose resolved device can use the NPU (`NPU`, `AUTO:NPU,GPU,CPU`, `HETERO:NPU,...`) is refused with `409` while another resident model already runs on a device that names the NPU. NPU contexts from two pipelines are not verified to coexist, so load the second model on CPU or GPU, or unload the other model first. `allow_resource_risk: true` overrides the refusal at your own risk.
- **Plain `AUTO` never uses the NPU.** OpenVINO excludes the NPU from AUTO's default list, so plain `AUTO` does not count as an NPU load. `AUTO:NPU,…`, `HETERO:NPU,…` and old `MULTI:NPU,…` do.
- **Embedding and reranker models default to CPU.** The native app loads them on CPU unless you pick a GPU, and never sends an NPU device for them. They are small, and on CPU they leave the NPU or GPU to the chat model.
- **Memory is estimated, not measured.** The preflight compares the hardware advisor's estimate for the new model with free RAM (keeping the larger of 2 GB or 15% in reserve before warning). GPU memory is only compared when the GPU reports its size. Watch memory after loading several models on a tight PC.
- None of these combinations has been exercised on Intel NPU hardware yet.

## Hardware Benchmarking

Use the benchmark feature to measure a converted catalog model on your own machine before choosing a default device:

```powershell
python -m app.server --benchmark --benchmark-model tinyllama-1.1b-chat-fp16 --benchmark-devices CPU,GPU,NPU,AUTO
```

Composite device strings contain commas, so use semicolons when listing several targets:

```powershell
python -m app.server --benchmark --benchmark-model tinyllama-1.1b-chat-fp16 --benchmark-devices "CPU;GPU;NPU;AUTO;AUTO:NPU,GPU,CPU"
```

You can also run the standalone module:

```powershell
python -m runtime.benchmark_runner --benchmark-model tinyllama-1.1b-chat-fp16 --benchmark-devices "CPU;AUTO:NPU,GPU,CPU"
```

The API and web UI expose the same runner:

```text
POST   /v1/benchmarks/run
GET    /v1/benchmarks
GET    /v1/benchmarks/latest
DELETE /v1/benchmarks
```

Results are saved under `benchmark/results/benchmarks.json` by default and that directory is gitignored. Set `OV_LLM_BENCHMARK_RESULTS` to move the JSON file.

The recommendation score prefers successful runs, lower first-token latency, higher tokens/sec, lower total latency, and reasonable load time. Treat it as a practical local hint, not a hardware guarantee. `AUTO`, `MULTI`, and `HETERO` are routing modes; they are not guaranteed to beat a direct `CPU`, `GPU`, or `NPU` target for every model or prompt.

In mock mode, benchmark results validate the server/UI flow only. Rerun on Windows with OpenVINO and converted model files for a real hardware recommendation. Ubuntu and Fedora remain experimental; validate CPU first, then test GPU/NPU only after OpenVINO lists those devices.
