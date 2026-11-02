# Device Support Notes

This project is Windows-first. Linux support is experimental and currently supports Ubuntu and Fedora.

## Target Matrix

| Platform | CPU | GPU | NPU |
|---|---|---|---|
| Windows | Supported | Supported if OpenVINO sees it | Primary target with compatible Intel hardware/drivers |
| Ubuntu experimental | Basic target | Experimental and driver-dependent | Experimental and driver-dependent |
| Fedora experimental | Basic target | Experimental and driver-dependent | Experimental and driver-dependent |

## Device Discovery

Linux configuration defaults to **CPU**, retaining explicit device selections and
checking them against OpenVINO's inventory before loading. Windows retains its NPU
default. A CPU default is not a claim that every host or model is supported. See
the [Linux evidence matrix](LINUX.md#build-and-qualification) for separate source,
packaged mock and real inference status.

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
./start_server.sh --check-devices
```

If OpenVINO does not list a device, the app cannot use it. `lspci`, Device Manager, or other system tools can provide useful hints, but OpenVINO discovery is the source of truth for this server.

## Device Modes

The server passes device strings to OpenVINO GenAI. Common examples:

```text
CPU
GPU
NPU
AUTO
AUTO:NPU,GPU,CPU
AUTO:GPU,NPU,CPU
MULTI:NPU,GPU,CPU
HETERO:NPU,GPU,CPU
```

`MULTI` and `HETERO` are experimental routing modes. They may help some workloads, but they do not promise faster single-prompt generation or additive multi-device performance.

## Several Resident Models and the NPU

InferBridge keeps one chat, one embedding and one reranker model loaded at the same time (see [API contract](API_CONTRACT.md#resident-model-slots-local-extension)). Device rules for that:

- **One engine on the Intel NPU.** A load whose device names the NPU (`NPU`, `AUTO:NPU,GPU,CPU`, `MULTI:NPU,...`) is refused with `409` while another resident model already runs on a device that names the NPU. NPU contexts from two pipelines are not verified to coexist, so load the second model on CPU or GPU, or unload the other model first. `allow_resource_risk: true` overrides the refusal at your own risk.
- **Plain `AUTO` is not checked.** The rule matches device strings, so plain `AUTO` is not treated as an NPU load even though OpenVINO may place it on the NPU. Choose `CPU` or `GPU` explicitly for embedding and reranker models when a chat model uses the NPU.
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
