# Benchmark Lab

Benchmark Lab is the comparative performance-testing view inside **Hardware Advisor → Benchmark Lab**. It extends InferBridge's existing benchmark runner and local benchmark store; it is not a second benchmarking subsystem.

The goal is to answer:

> How fast does this model actually run on this PC?

Benchmark Lab is local-first. Model prompts, benchmark samples, hardware evidence, and history stay on the InferBridge machine unless the user deliberately copies or downloads a result.

## Eligible models

Benchmark Lab benchmarks **locally prepared generation models**.

- Generation models can be compared one or many at a time.
- Embedding models are not treated as generation models.
- Selecting a model never starts a download or conversion.
- A model that is not prepared is shown as **Needs preparation** and requires an explicit preparation action.
- Mock mode treats catalog generation models as available for deterministic UI/API/CI validation, but those results are marked synthetic and are never hardware certification evidence.

Where model metadata exists, Benchmark Lab can carry architecture metadata such as total parameters, active parameters per token, expert counts, precision, and estimated weight footprint. Missing metadata remains missing; InferBridge does not scrape or invent it. For Mixture-of-Experts models, active parameters describe compute activity, not the memory needed to hold the model's weights.

## Devices

Direct benchmark choices come from the devices OpenVINO exposes on the current machine:

- CPU
- GPU
- NPU

`AUTO` is also available as an OpenVINO routing target. Advanced users can add an existing composite OpenVINO expression through the Advanced disclosure.

Requested and actual devices are always distinct fields. A result can therefore show:

```text
GPU → GPU
AUTO → GPU
NPU → unavailable
```

InferBridge never treats a requested routing expression as proof that the same physical device executed the graph.

## Presets

The browser provides three presets:

| Preset | Warm-up runs | Measured runs | Output-token target |
|---|---:|---:|---:|
| Quick | 1 | 3 | 32 |
| Standard — Recommended | 1 | 5 | 64 |
| Thorough | 2 | 8 | 128 |

Advanced controls can override measured-run count, output-token target, benchmark prompt, and an advanced device expression. A non-preset combination is stored as `custom`.

The public benchmark API remains backward compatible. Existing clients can continue to send a single `model`, while newer clients can send `models` for a comparison matrix.

## Metric definitions

### Generation speed / decode throughput

`decode_tokens_sec` is the preferred Benchmark Lab throughput metric.

With InferBridge's current streaming abstraction, TTFT measures from the beginning of generation until the first emitted output token/chunk. Decode throughput therefore excludes the first output token and TTFT:

```text
decode_tokens_sec =
    (completion_tokens - 1)
    / (total_generation_seconds - time_to_first_token_seconds)
```

This is reported only when at least two completion tokens exist and the post-first-token interval is positive.

The older `tokens_sec` field remains for API and history compatibility. Its definition is unchanged:

```text
tokens_sec =
    completion_tokens / total_generation_seconds
```

That legacy value includes TTFT and should not be interpreted as pure decode throughput.

### Prefill throughput

InferBridge does **not** derive prefill throughput from TTFT. TTFT includes prompt processing plus the work required to produce the first output token, so dividing prompt tokens by TTFT would overstate precision.

`prefill_tokens_sec` is therefore `null` / **Unavailable** until the OpenVINO GenAI path exposes a trustworthy prompt-processing timing boundary.

### First token / TTFT

`time_to_first_token_ms` measures from the generation call starting until the first non-empty streamed output is observed. It represents responsiveness, including prompt processing and first-token decode.

### Total inference latency

`total_latency_ms` measures the full generation operation for a measured run.

### Load time

`load_time_ms` is captured separately from inference measurements. It represents temporary engine construction/load for the benchmark combination and can include first-load OpenVINO compilation cost when that cost occurs.

Do not compare load time and steady-state generation throughput as though they are the same performance dimension. A model can be expensive to load and fast once resident.

### Peak process RAM

`peak_process_ram_mb` is the highest observed resident set size (RSS) of the InferBridge process while the model/device combination is being benchmarked.

It is **not** GPU VRAM, NPU memory, or device-local memory.

If process RSS cannot be measured reliably, the value is unavailable instead of fabricated.

## Repeated runs and statistics

Warm-up generations are executed before measured generations and are not included in statistics.

Measured samples retain enough data to calculate:

- median
- minimum
- maximum
- population standard deviation
- coefficient of variation (CV)
- sample count

Primary displayed throughput, TTFT, and total latency use the **median** of measured runs.

Stability is a descriptive treatment based on throughput CV:

- `stable`: CV ≤ 5%
- `moderate`: CV > 5% and ≤ 12%
- `variable`: CV > 12%

The raw range and CV remain available so the label is not the only evidence. These categories are operational guidance, not claims of laboratory-grade scientific precision.

## Result scoring

The existing balanced benchmark score remains the source for the **Best balanced** conclusion. Successful combinations are ranked ahead of failed combinations.

For Benchmark Lab methodology version 2, the score prefers:

1. decode throughput when available, otherwise legacy total throughput
2. low TTFT
3. low total latency
4. lower load time, with an additional penalty for very high load cost

The result header also reports independent leaders for:

- fastest generation
- fastest first token
- best balanced

These are derived from the measured matrix, not from static catalog preferences.

## Reproducibility metadata

Manual Benchmark Lab runs retain:

- InferBridge version
- benchmark schema and methodology version
- model ID and source model
- precision
- optional architecture metadata when already known
- requested device
- actual device
- OpenVINO version
- OpenVINO GenAI version
- relevant driver versions exposed by OpenVINO
- hardware fingerprint
- prompt token count
- output-token target
- actual completion tokens
- warm-up count
- measured-run count
- timestamp
- per-run measured samples
- aggregate statistics

The benchmark store's outer JSON schema remains version `1` for backward reading. Benchmark Lab adds additive run/result fields and a separate `methodology_version`, so existing benchmark history is not discarded during upgrade.

## History and previous-run comparison

Manual Benchmark Lab runs are shown in a restrained recent-history list. Automatic post-load advisor benchmarks remain in the same local store but are not mixed into the manual-run history UI.

A previous-run delta is shown only when the important comparison conditions match:

- same hardware fingerprint
- same benchmark methodology version
- same output-token target
- same measured-run count
- same warm-up count
- same model ID
- same source model
- same precision
- same requested device
- same reported actual device

If those conditions do not match, Benchmark Lab suppresses the percentage comparison rather than presenting incompatible evidence as a regression or improvement.

This makes Benchmark Lab suitable for before/after OpenVINO, OpenVINO GenAI, and driver qualification on the same PC.

## Advisor integration

Hardware Advisor consumes only compatible real-hardware benchmark evidence.

A benchmark can influence recommendations only when:

- the result succeeded
- it is not synthetic/mock evidence
- model identity and precision match the current catalog entry
- hardware fingerprint matches the current machine/runtime fingerprint
- a device-specific recommendation has both requested and actual execution on the same direct device

When current evidence exists, the Advisor view exposes **Measured on this PC** with generation speed, first-token time, and requested → actual device.

Synthetic mock runs remain visible in Benchmark Lab history but are ignored as recommendation evidence.

## API

### `POST /v1/benchmarks/run`

Backward-compatible single-model request:

```json
{
  "model": "tinyllama-1.1b-chat-fp16",
  "devices": ["CPU"],
  "max_tokens": 64,
  "runs": 5
}
```

Multi-model request:

```json
{
  "models": [
    "tinyllama-1.1b-chat-fp16",
    "qwen2.5-0.5b-int4"
  ],
  "devices": ["CPU", "GPU", "NPU"],
  "max_tokens": 64,
  "runs": 5
}
```

`model` and `models` can coexist. InferBridge combines them and deduplicates model IDs while preserving first occurrence. Devices are also validated and deduplicated.

The server infers the preset and warm-up count from the measured-run/output target used by the UI. Existing API callers using one measured run remain valid and use no implicit warm-up.

A failed model/device combination is recorded as a failed result row. Safe failures do not abort the remaining matrix.

### `GET /v1/benchmarks`

Returns locally persisted runs newest-first.

### `GET /v1/benchmarks/latest`

Returns the newest persisted run.

### `DELETE /v1/benchmarks`

Clears saved benchmark history. The UI requires a deliberate confirmation before calling this destructive route.

All benchmark routes remain protected by InferBridge's normal API-key policy.

## Progress and concurrency

Benchmarking uses the existing bounded local activity-event stream returned by `/v1/system/status`. While a manual run is active, the UI polls that existing status route at a restrained interval and reports stages such as:

- preparing
- loading
- preparing prompt
- warming up
- prefill
- generating
- finalizing

It also reports the current model/device combination and matrix position.

The browser disables the run action while its request is active. The backend serializes benchmark suites per manager instance so duplicate clicks or competing clients cannot run two full benchmark matrices concurrently against the same process.

A failed combination can continue to the next safe combination.

## Copy and JSON export privacy

**Copy results** produces a compact Markdown summary. **Download JSON** produces a structured technical export.

Both exports deliberately include only benchmark-relevant fields such as:

- CPU description
- installed RAM
- InferBridge/OpenVINO/OpenVINO GenAI versions
- device labels and driver versions
- benchmark methodology
- model/device metrics and statistics

They deliberately exclude:

- user name
- hostname
- API keys
- Hugging Face tokens
- local filesystem paths
- serial numbers
- benchmark prompt text
- unrelated diagnostics
- persisted hardware fingerprint

The local benchmark store retains its hardware fingerprint for compatibility matching, but the share/export helpers do not expose that stable local identifier.

## Mock mode

Mock mode exercises:

- API contracts
- model/device matrix state
- progress rendering
- repeated-run aggregation
- history
- failure rows
- copy/export formatting
- responsive UI
- keyboard behavior

Every mock result is marked synthetic. Synthetic evidence is not hardware certification and does not influence Hardware Advisor recommendations.
