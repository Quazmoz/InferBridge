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

The browser's **Context budget** panel and native WinUI **Settings → Per-model
context window** use the same authoritative Python model catalog and OpenVINO
engine lifecycle. They display the current total context, prompt budget, output
reserve, recommended baseline, model-architecture limit when available, and
eligible size choices. Settings survive a restart; chat history is not altered.

`GET /v1/models/context?model=<id>` returns options and advisory estimates.
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
