# Context budget visibility

InferBridge can inspect a chat request before generation and show how the selected loaded model will use its context window.

The WebGUI displays a compact context indicator beside the existing token counter. Open it to review:

- prompt tokens used
- the model's prompt budget and full context window
- requested and currently available output tokens
- whether the requested output will be capped
- retained and omitted message counts
- older whole turns that will be omitted
- a bounded preview of omitted messages
- the conservative token allowance used for pending image attachments

## API

```http
POST /v1/chat/context-budget
```

The request accepts the same core fields as Chat Completions, plus `image_count` for browser attachments that have not yet been inserted into the request message.

```json
{
  "model": "tinyllama-1.1b-chat-fp16",
  "messages": [
    {"role": "system", "content": "Answer concisely."},
    {"role": "user", "content": "Summarize the discussion."}
  ],
  "max_tokens": 512,
  "image_count": 0
}
```

The selected model must already be loaded because the preflight uses that engine's actual tokenizer and chat template.

## Accuracy and retention behavior

For text messages, the preflight uses the same components as generation:

1. OpenAI-style message normalization
2. tool-instruction injection when tools are supplied
3. the loaded model's chat template
4. the loaded model's tokenizer
5. InferBridge's existing whole-turn sliding-window algorithm
6. the same eight-token generation safety allowance

Leading system instructions remain pinned. When the request exceeds the prompt budget, InferBridge retains the newest user-led turns and removes complete older turns. It does not retain an assistant answer after omitting the user message that prompted it.

Pending browser images use the same conservative per-image token reserve as vision generation. That portion is an estimate because the images have not yet entered the server-side vision prompt. The UI labels attachment token use with an approximation marker.

The preflight does not change generation behavior. It reports what the existing prompt builder will do.

## Adaptive model context configuration

Every registered model has its own saved context window, and it can be read
and changed **without loading the model**. One shared editor serves every
surface:

- **Native Model library:** each model card shows `Context: 8,192 tokens` with a
  **Context…** button. Downloaded (unloaded) models also offer **Load with
  context…**, which opens the same editor and loads with the chosen value.
- **Native Chat:** the model/device bar shows the selected model's context with
  **Context…** (or **Load with context…** for an unloaded model), and the
  Inference settings pane shows it next to the *Context used* ring with
  **Change…**. An ordinary load states which saved context it uses.
- **Native Settings → Per-model context window:** a list of every model and its
  context, each with **Edit…**.
- **Browser Model Library:** the same per-model line, editor and **Load with
  context…** on each registered model's card.

The editor shows the current value, the recommended value and the model's
architecture maximum (from the converted `config.json`), offers presets (2K, 4K,
8K, 16K, 32K, 64K, 128K, capped at the model maximum) plus a custom whole number,
estimates the FP16 KV cache for any value, and marks values above the
recommendation as **trial**. Invalid values get a friendly message before
anything is sent. For a loaded model the primary action is **Apply now (reloads
engine, chat kept)**; otherwise **Save for next load** ("Used next time this
model loads"). Connect a tool and Connection Hub token limits follow the saved
value immediately.

`GET /v1/models/contexts` lists every registered model (loaded or not) with its
configured, recommended and architecture-limit values, custom-value bounds, a
`trial` flag, `loaded`, and `applies` (`now` or `next-load`); embedding models
are listed with `configurable: false`. `POST /v1/models/load` accepts an optional
strict `context_tokens`: for an unloaded model it is validated and persisted
exactly like the route below before the load is scheduled; for an already
loaded model a different value is refused with 409 and a pointer to Apply now.

`GET /v1/models/context?model=<id>` returns options, presets, custom-value bounds
(`minimum_context_tokens`, `maximum_context_tokens`), `applies`, and advisory
estimates for any registered model, loaded or not.
`POST /v1/models/context` accepts a strict JSON body:

```json
{"model":"tinyllama-1.1b-chat-fp16","context_tokens":4096,"expected_context_tokens":2048}
```

The `expected_context_tokens` compare-and-set guard rejects a stale client with
HTTP 409. Embedding models, invalid values, and requests exceeding an explicit
converted `config.json` architecture limit are rejected. Without trustworthy
architecture metadata, values above the model's original recommended context
are unavailable. Legacy `models.json` entries remain valid: the original
`max_context_len` becomes the saved recommended baseline only when a context
preference is successfully applied. **Restore recommended** preserves that
baseline across restarts. If new architecture metadata contradicts a previously
saved context, the UI marks that *existing–unverified* value as read-only and
offers only validated reductions. Restore is disabled if the old recommendation
is no longer eligible.

For a loaded model, an eligible change compiles a new pipeline under the
shared model-load lock, waits for a safe per-model handoff, then atomically
persists the catalog and switches engines. Build, persistence, cancellation,
or stale-state failure leaves the old engine and config active. Requests using
other models can continue. If no engine is loaded, the validated preference is
saved and applied on the next load. Do not blindly retry after a client-side
timeout: refresh authoritative state first.

Direct NPU LLMs use the installed compatibility layer's `MAX_PROMPT_LEN` and
`MIN_RESPONSE_LEN`; their sum is the configured total. CPU/GPU context budgets
are enforced in the existing chat prompt builder, but acceptance does **not**
prove memory availability or the ability to generate at that depth. An option
above the recommendation is labeled *eligible–unverified*. No KV precision
toggle is exposed.

Where architecture and hardware metadata permit, the options also show a
**host-RAM planning floor** (converted weights plus illustrative FP16 KV cache,
overhead, and other loaded models), alongside an estimated memory reserve and
available host RAM. These values do not represent NPU allocated memory, GPU
VRAM requirements, or successful runtime qualification. Missing KV metadata
results in *unverified memory*, not an artificially precise fit estimate.

FP16 KV-cache figures are calculated only when the converted model provides
layer/head/hidden-dimension metadata, and represent illustrative K+V allocation,
not measured OpenVINO or NPU memory. Actual converted weight footprint is
distinguished from an estimate. The Hardware Advisor's host-memory estimate is
a lower bound when KV architecture is unavailable, never a fit guarantee.

## WebGUI actions

The expanded context panel offers:

- **Start new chat from here**: creates a fresh chat while preserving the current draft.
- **Reduce output to fit**: lowers the output setting to the currently effective limit.
- **Refresh**: reruns the exact preflight immediately.

The indicator updates after draft, system-instruction, output-limit, model, chat, generation, or attachment changes. Requests are debounced and older in-flight preflights are cancelled.

## Privacy and security

- The endpoint follows the configured API-key policy.
- Cross-site browser requests are rejected.
- Existing request-body limits apply.
- Prompt text is processed in memory and is not persisted by the context-budget feature.
- The preflight does not increment generation metrics.
- Omission previews are bounded to 12 messages and 180 characters per message.
- The server does not return rendered prompts, tokenizer IDs, image data, local paths, or model files.
