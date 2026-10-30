# Context and memory: using larger context in InferBridge

Short answer: every model's context window is a per-model setting you can raise
yourself, up to what the model's own `config.json` allows (and never above
262,144 tokens). On **CPU and GPU** that is usually far more than the default.
On the **NPU** larger values work too, but each value compiles a fixed-size
graph, so keep it modest. To fit more context into the same memory on CPU or GPU,
set **KV cache precision** to INT8 (`u8`) or INT4 (`u4`).

The shipped defaults (2,048 or 4,096 tokens for most bundled models) are
deliberately small. They are NPU-safe starting points, not limits.

For how a single chat request is fitted into the window (prompt budget, omitted
turns, output reserve), see [Context budget](CONTEXT_BUDGET.md).

## How to use a larger context

### Native app (Windows)

The same context editor opens from every place a model is shown:

- **Model library**: each card shows `Context: N tokens` with **Context…**.
  Models that are downloaded but not loaded also offer **Load with context…**.
- **Chat**: the model bar shows the selected model's context with
  **Context…** (or **Load with context…**), and the Inference settings pane has
  **Change…** beside the *Context used* ring.
- **Settings → Per-model context window**: every model with **Edit…**.

In the editor, pick a preset (2K up to 128K, plus **max for this model** when the
model's `config.json` states its limit) or type a whole number, choose a **KV
cache precision**, and read the estimate. Values above the recommendation are
marked **trial**. For a loaded model, **Apply now (reloads engine, chat kept)**
rebuilds the pipeline; if the rebuild fails, the old engine and settings stay
active. For an unloaded model the value is saved and **used next time this
model loads**.

While a value is being applied the editor stays open: its controls and buttons
are locked (no double submits, no Cancel mid-rebuild) and a progress ring reads
**Applying 16K context… rebuilding engine** for a loaded model, or **Saving 16K
context…** for an unloaded one. The model card, the Chat model bar and the
Inference settings pane show the same line with a small ring. On success the
editor briefly shows **Context set to 16K** and closes; on failure the controls
come back with the engine's friendly error and the old engine keeps running.
Narrator announces the start, success and failure. While the selected chat
model rebuilds, **Send** and **Regenerate** pause with a note in the composer;
your draft is kept. An apply started from the browser (the engine reports the
model as `changing`) shows the same busy line after the next list refresh.

### Browser UI

Open **Model Library**. Each registered model's card has the same context line,
editor, presets, KV cache precision picker and **Load with context…**.
Applying shows a spinner and the same status text, locks the editor
(`aria-busy`, polite live status), then shows **Context set to 16K** before the
cards refresh; an error keeps the editor open with the message. The chat
composer shows a rebuilding strip for the selected loaded model whichever
surface applied the change (the Model Library or the context budget panel), and
holds Send and Regenerate with a toast until the engine confirms.

### API

All routes are on the local server (default `http://127.0.0.1:8000`) and need
the same authorization as the rest of the API when a key is configured.

List every model's saved, recommended and maximum context (loaded or not):

```http
GET /v1/models/contexts
```

Read the options, presets, bounds, KV choices and estimates for one model:

```http
GET /v1/models/context?model=qwen2.5-7b-int4
```

Change it (applies now if loaded, otherwise on next load).
`expected_context_tokens` is a compare-and-set guard: send the value you last
read, and a stale client gets HTTP 409 instead of overwriting someone else's
change.

```http
POST /v1/models/context
Content-Type: application/json

{
  "model": "qwen2.5-7b-int4",
  "context_tokens": 32768,
  "expected_context_tokens": 4096,
  "kv_cache_precision": "u8"
}
```

Or set it as part of a load of an unloaded model:

```http
POST /v1/models/load
Content-Type: application/json

{"model": "qwen2.5-7b-int4", "device": "GPU", "context_tokens": 32768, "kv_cache_precision": "u8"}
```

A load request with a *different* `context_tokens` for a model that is already
loaded is refused with 409; use `POST /v1/models/context` (Apply now) instead.

OpenAI-compatible clients do not need to change anything: chat requests use the
model's saved window, and Connection Hub / **Connect a tool** report the new
`context_tokens` to coding tools straight away. Agentic coding tools generally
want 16K–32K or more, which in practice means CPU or GPU.

## KV cache precision

The KV cache holds the attention keys and values for every token in the window.
It grows linearly with context, so at long context it, not the model weights,
is usually what runs out of memory.

| Setting | What is sent to OpenVINO | Estimated size vs FP16 |
|---|---|---|
| **Auto** (default) | Nothing; OpenVINO picks (often INT8 on CPU/GPU) | Shown at the FP16 upper bound, because OpenVINO does not report its choice |
| **FP16** (`f16`) | `KV_CACHE_PRECISION=f16` | 1x (no compression) |
| **INT8** (`u8`) | `KV_CACHE_PRECISION=u8` | about 0.57x |
| **INT4** (`u4`) | `KV_CACHE_PRECISION=u4` | about 0.32x |

- **CPU and GPU only.** The property is sent only when the model loads on one
  physical CPU or GPU. On NPU, `AUTO` and composite device expressions it is not
  sent; the saved choice is kept and used the next time the model loads on CPU
  or GPU.
- **Estimates, not measurements.** The editor's KV figures are architectural
  estimates from the model's `config.json` (layers, KV heads, head size): 2 bytes
  per value for FP16, about 1.125 for INT8 and about 0.625 for INT4 including
  scale overhead. The ratios follow Intel's published Llama-3-8B GPU numbers.
  They are not measured device memory and not a promise the context will fit.
- **Accuracy caveat.** Compressing the KV cache trades some accuracy for memory.
  INT8 is usually close to FP16; INT4 can measurably degrade answers, especially
  on long documents, exact recall and code. If quality drops, step back to INT8
  or FP16 before blaming the model.
- **OpenVINO version.** INT4 (`u4`) KV cache on GPU needs **OpenVINO 2026.2 or
  later**. If the runtime or device rejects a precision, InferBridge says so,
  keeps the previous engine (or fails the load cleanly) and asks you to switch
  back to Auto or FP16.

## Adjusting the bundled models' context

Bundled and catalog models start at their recommended value (`max_context_len`
in `models.json`, mostly 2,048 or 4,096 tokens with a 512 or 1,024-token output
reserve). To raise one, use the editor or the API above; nothing needs to be
reconverted. The editor will offer values up to the converted model's
architecture limit from its `config.json` (`max_position_embeddings`,
`n_positions` or `n_ctx`, and `sliding_window` when present, whichever is
smallest). If a converted model has no such metadata, the editor cannot go
above the recommendation.

To go back, choose the preset marked recommended. Avoid hand-editing
`models.json` for this: the editor and API validate the value, keep the
recommended baseline, and apply the change safely.

## Why the defaults are small

- **NPU uses static shapes.** A direct NPU LLM compiles a fixed graph with
  `MAX_PROMPT_LEN` = context minus the output reserve and `MIN_RESPONSE_LEN` =
  the output reserve. Every new value means a new compile, and larger values use
  more NPU memory and compile time. A small default keeps first loads on the NPU
  quick and reliable.
- **CPU and GPU use dynamic shapes.** They can go up to the model's
  `config.json` limit; the practical ceiling is RAM/VRAM for the KV cache.
- **A hard cap of 262,144 tokens** applies everywhere (API, editors and
  `models.json`), whatever the model claims.
- **Defaults were not raised on purpose.** Raising them would silently change
  memory use, load time and NPU compile behavior for people whose setups work
  today. Larger context is opt-in per model instead.

Accepting a value proves only that it is valid for the model, not that your
hardware can compile it or generate at that depth. Values above the
recommendation stay labeled *trial* until you have tried them.

## NPU notes

- Prefer modest windows on NPU (the recommended value, or a few thousand tokens
  more). For long-context work, load the same model on GPU or CPU.
- KV cache precision does not apply on NPU; the NPU plugin manages its own KV
  layout.
- **Channel-wise INT4 for NPU conversions.** When InferBridge converts a model
  for the NPU it uses symmetric INT4 with ratio 1.0. The group size is 128 by
  default, and **channel-wise (group size -1)** for Phi-4-mini, for models above
  about 4B parameters, and for models of 3B or more on NPU 3720 (Meteor Lake).
  This follows Intel's NPU guidance (group size 128 for smaller models,
  channel-wise for larger ones). An explicit group size you choose always wins.
- **Phi-4 Mini on NPU is unverified.** Older NPU drivers and NPU 3720 can reject
  its compilation (for example `ZE_RESULT_ERROR_INVALID_ARGUMENT`, or
  "Failed to compile. No more devices are left!"). When a load fails this way,
  InferBridge offers **Update Intel NPU driver**, **Load on GPU instead**, and
  **Reconvert for NPU (channel-wise INT4)** in both the browser and native app.
  It never switches devices on its own. Related public reports:
  [openvino.genai#2082](https://github.com/openvinotoolkit/openvino.genai/issues/2082)
  (a newer NPU driver fixed it for the original reporter; a community
  channel-wise INT4 conversion was reported working there) and InferBridge #65.
- Diagnostics exports include the **NPU driver version** (`NPU_DRIVER_VERSION`),
  which is the first thing to check for an NPU compile failure.

## Contributing benchmarks

Real numbers from real machines are the only way to turn *trial* into
*known-good*. InferBridge's own [Benchmark Lab](BENCHMARK_LAB.md) (Hardware
Advisor → Benchmark Lab) measures TTFT and tokens/s locally; nothing is sent
anywhere unless you share it.

To submit results, either:

- open an issue with the **Benchmark results** form (or a blank issue with the
  template below), or
- open a pull request adding a Markdown file to `docs/benchmarks/`, named
  `<cpu-or-platform>-<model>-<device>-<context>.md` (for example
  `core-ultra-7-155h-qwen2.5-7b-int4-gpu-32k.md`).

If you prefer not to use GitHub, use **Send feedback** on the
[InferBridge support page](https://consultant.quinnfavo.com/apps/inferbridge#feedback)
and paste the same template. Please remove anything private (paths, user names)
first. Submitted results are recorded as user reports, not certification.

Copy-paste template:

```markdown
### InferBridge benchmark result

- **InferBridge version:** (e.g. 1.1.1-dev.1, from diagnostics or `GET /health`)
- **Hardware:** (CPU / GPU / NPU, e.g. Core Ultra 7 155H, Arc iGPU, NPU 3720; RAM)
- **OS:** (e.g. Windows 11 24H2)
- **Driver(s):** (GPU driver; NPU driver, NPU_DRIVER_VERSION from diagnostics)
- **OpenVINO version:** (OpenVINO / OpenVINO GenAI, from diagnostics)
- **Model:** (id or Hugging Face repo, and weight format / group size, e.g. INT4 sym, group size -1)
- **Device:** (requested → actual, e.g. GPU → GPU)
- **Context window:** (tokens configured; prompt length actually used)
- **KV cache precision:** (Auto / f16 / u8 / u4)
- **TTFT:** (time to first token, ms)
- **Tokens/s:** (generation throughput)
- **Peak RAM:** (GB)
- **Peak GPU memory:** (GB, or n/a)
- **Notes:** (load/compile time, errors, quality observations, Benchmark Lab or manual)
```
