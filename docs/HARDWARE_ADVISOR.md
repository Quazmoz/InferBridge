# Hardware-aware model advisor

The hardware advisor turns the existing benchmark and telemetry systems into a **Best model for this PC** workflow. It is local-only and does not upload hardware details, model metadata, prompts, or benchmark results.

The browser exposes two related views from the same entry point:

- **Advisor** answers: “What model is appropriate for this PC?”
- **Benchmark Lab** answers: “What actually performs best on this PC?”

Advisor remains the default view. Benchmark Lab extends the same benchmark persistence and evidence pipeline rather than creating a parallel subsystem. See [Benchmark Lab](BENCHMARK_LAB.md) for methodology, API, statistics, history comparison, and export details.

## What it evaluates

The advisor collects and fingerprints the hardware state that materially affects OpenVINO model fit:

- operating system and architecture
- CPU name, physical cores, logical cores, and reported maximum frequency
- installed and currently available RAM
- free disk space on the configured model volume; the existing top-level status payload continues to report total converted-model footprint
- OpenVINO-visible CPU, GPU, and NPU devices
- device full names, architectures, optimization capabilities, and driver versions when exposed by the installed OpenVINO plugin
- installed OpenVINO and OpenVINO GenAI versions

The fingerprint intentionally excludes volatile utilization values. It is used only to avoid treating benchmark evidence from a materially different machine or driver stack as current evidence.

## Estimates and measured evidence

Before a model is downloaded, the advisor estimates:

- source download size
- converted OpenVINO IR size
- runtime memory including a conservative KV-cache allowance
- first-load compilation cost
- a safe initial context length and output-token limit

These values are labelled as estimates. After conversion or loading, the real model-directory footprint is measured in a worker thread and replaces the converted-size estimate.

When a **real, non-synthetic** benchmark exists for the same model identity, precision, direct device, and current hardware fingerprint, observed load time and generation throughput take precedence. Benchmark Lab methodology version 2 prefers measured decode throughput where available while retaining the older `tokens_sec` field for backward compatibility.

Evidence from a materially different fingerprint is not applied to current recommendations. Mock/synthetic Benchmark Lab runs are retained for UI/API/CI validation but are never consumed as hardware recommendation evidence.

When matching evidence exists, the Advisor view surfaces a **Measured on this PC** treatment showing:

- generation speed
- first-token latency
- requested → actual device

Warnings are grouped as:

- `info`: preparation notes such as gated model access or long compilation
- `warning`: the model may work, but the selected device, memory headroom, or context is risky
- `blocking`: the requested device is absent or available RAM/disk is below the conservative requirement

The browser UI asks for confirmation before starting a catalog or custom-model conversion when warning or blocking preflight findings exist.

## Saved profiles

The built-in UI persists the selected profile in browser `localStorage`:

| Profile | Goal |
|---|---|
| Fastest | Prefer measured generation throughput and first-token responsiveness. |
| Balanced | Blend estimated quality, speed, memory fit, and benchmark evidence. |
| Best quality | Prefer the highest-capability model that does not fail preflight. |
| Lowest memory | Prefer the smallest compatible runtime-memory footprint. |
| Lowest power | Prefer compact NPU workloads, then efficient GPU/CPU fallbacks. |

The selected profile and automatic-routing preference are saved locally. The browser keeps a concrete loaded model selected so existing load, unload, attachment, and chat-queue behavior remains intact, then sends the `auto:<profile>` alias for compatible text-only requests.

## Automatic model routing

Any OpenAI-compatible generation request may use one of these model aliases:

```json
{
  "model": "auto",
  "messages": [{"role": "user", "content": "Summarize this."}]
}
```

`auto` is equivalent to `auto:balanced`. Explicit aliases are:

```text
auto:fastest
auto:balanced
auto:best-quality
auto:lowest-memory
auto:lowest-power
```

Automatic routing selects only from **currently loaded, non-embedding models**. It never initiates a download or load implicitly. If no compatible generation model is loaded, the API returns the existing no-models-loaded error path with an advisor-specific explanation.

The selected engine still goes through normal prompt budgeting, image capability validation, generation locking, cancellation, metrics, and error handling.

## Automatic short benchmark

After a real text-generation or vision model finishes loading, the manager schedules a short local streaming benchmark unless a successful automatic result already exists for the same model, device, and hardware fingerprint within six hours.

The automatic benchmark records:

- requested and actual device
- model load time when it can be measured without conversion or queue delay
- time to first token
- total latency
- completion tokens
- backward-compatible tokens per second
- hardware fingerprint

It is appended to the existing benchmark JSON store and is visible through the existing benchmark APIs. It does not replace the full comparative Benchmark Lab suite.

## Benchmark Lab

Benchmark Lab is integrated into this same dialog with accessible `Advisor | Benchmark Lab` tabs.

The normal workflow is:

1. choose one or more locally prepared generation models
2. choose direct OpenVINO devices and optionally `AUTO`
3. keep **Standard — Recommended** or choose Quick/Thorough
4. run the matrix while lifecycle progress is shown
5. compare generation speed, TTFT, load cost, process memory, stability, and requested → actual routing
6. inspect a previous compatible run
7. copy privacy-safe Markdown or download JSON

Warm-ups are excluded from statistics. Measured samples retain medians, ranges, standard deviation/CV, and sample count. Cold/load time is kept separate from inference timing. Prefill throughput is shown as unavailable when the current engine abstraction cannot measure a trustworthy prefill boundary.

Manual Benchmark Lab history uses the same local benchmark file as automatic advisor evidence. The outer store remains schema version 1; new methodology/result fields are additive so older history stays readable.

For exact metric definitions and reproducibility rules, see [Benchmark Lab](BENCHMARK_LAB.md).

## Status contract

The existing `GET /v1/system/status` response includes advisor data under:

```text
metrics.advisor
```

Important fields:

```text
metrics.advisor.hardware
metrics.advisor.profiles
metrics.advisor.loaded_profiles
metrics.advisor.models
metrics.advisor.auto_model_examples
```

Each item in `models.available` also includes an `advisor` object with that model's current preflight result.

Benchmark Lab reuses the existing bounded `events` collection in `/v1/system/status` for in-progress stage text. It does not add heavyweight hardware scanning or a new continuously polled status endpoint.

## Safety and interpretation

The advisor deliberately avoids claiming universal device compatibility. Intel NPU, integrated GPU, system memory, driver, model graph, precision, context, and OpenVINO release all affect whether a model compiles and performs well. A successful benchmark on the current hardware is stronger evidence than a catalog default or estimate.

Requested and actual device are never treated as equivalent without evidence. A routed `AUTO`, `MULTI`, or `HETERO` request is not direct-device certification.

For MoE metadata, active parameters are treated as a compute characteristic only. They are not used as a substitute for total weight footprint or model memory requirements.
