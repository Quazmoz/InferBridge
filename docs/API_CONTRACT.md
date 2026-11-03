# InferBridge API contract

InferBridge implements a practical subset of the OpenAI API plus local model, device, benchmark, diagnostics, onboarding, and desktop-operation routes. CI tests the contract in mock mode, and `scripts/validate_windows.ps1` can certify it against real Windows hardware.

## Compatibility levels

| Level | Meaning |
|---|---|
| Mock contract | Route, schema, streaming, lifecycle, and error behavior tested without OpenVINO hardware. |
| Real CPU certified | A Windows report completed with mock mode disabled on CPU. |
| Real GPU certified | A Windows report completed on an OpenVINO-visible Intel GPU. |
| Real NPU certified | A Windows report completed on an OpenVINO-visible Intel NPU. |
| Client verified | The actual external client was manually connected in addition to black-box contract validation. |

A mock contract result is not evidence that a driver or hardware target works.

## Local model context configuration (not OpenAI routes)

- `GET /v1/models/contexts`: every registered model, loaded or not, with
  `configured_context_tokens`, `recommended_context_tokens`,
  `model_architecture_limit_tokens`, custom-value bounds, `trial`, `loaded`,
  `applies` (`now`/`next-load`) and `configurable` (false for embedding models).
- `GET /v1/models/context?model=<id>`: works for any registered model (loaded or
  not); adds `presets`, `minimum_context_tokens`, `maximum_context_tokens` and
  `applies` to the current total, prompt/output budgets,
  recommended baseline, optional converted-model architecture bound, candidate
  choices, and explicitly unverified memory guidance.
- `POST /v1/models/context`: `{ "model": "<id>", "context_tokens": 4096,
  "expected_context_tokens": 2048 }`. Integers are strict; the expected value
  rejects stale clients (409). Validation failures return 400/422. Apply
  succeeds only after engine replacement (if loaded) and durable catalog save.
  Compilation or persistence failure retains the prior engine/config and
  returns a sanitized 409 response. No inference is started by this route.
  For an unloaded model the value is only persisted and used on its next load.
- `POST /v1/models/load` accepts optional strict `context_tokens`. For an unloaded
  model it is validated and persisted as above before the load is scheduled
  (400/409 leave the catalog and load state unchanged); a different value for an
  already loaded model returns 409 (use the context route to apply now).

Hardware eligibility beyond the original recommendation must be certified on
the requested OpenVINO runtime/device. See [Context budget](CONTEXT_BUDGET.md).

## OpenAI-compatible routes

### `GET /v1/models`

Returns an OpenAI-style model list. InferBridge includes local lifecycle status as an additional field.

Exact model IDs are strict across Chat Completions, Responses, embeddings and rerank. An unknown exact ID returns `404`; InferBridge never silently substitutes the configured default or another loaded engine. The documented `model=auto` and `model=auto:<profile>` advisor selectors remain explicit exceptions that intentionally choose among compatible loaded generation models.

### Resident model slots (local extension)

InferBridge keeps at most **one resident model per role**: one `chat` model (text generation or vision), one `embedding` model and one `rerank` model. The role always comes from the catalog `backend` (`openvino-embeddings` → `embedding`, `openvino-rerank` → `rerank`, everything else → `chat`); clients never choose it. Requests keep routing by their exact `model` id, each engine keeps its own lock, so slots serve concurrently while each slot is serialized.

`GET /v1/models` adds three local fields per `data[]` item, `role`, `loaded` and `device` (engine-reported, `null` when not loaded), and a top-level `slots` array. `GET /v1/models/slots` returns the same array:

```json
{"slots": [
  {"role": "chat", "label": "Chat", "model": "qwen2.5-1.5b-int4", "name": "Qwen2.5 1.5B Instruct INT4",
   "device": "GPU.0", "status": "loaded", "busy": false, "can_unload": true,
   "memory_estimate_gib": 1.6, "loading_model": null},
  {"role": "embedding", "label": "Embeddings", "model": null, "name": null, "device": null,
   "status": "empty", "busy": false, "can_unload": false, "memory_estimate_gib": null, "loading_model": null},
  {"role": "rerank", "...": "..."}
]}
```

`status` is `empty`, `loading`, `loaded` or `switching` (a device switch of the resident model). `memory_estimate_gib` is a hardware-advisor estimate, not measured memory. `GET /v1/models/slots?model=<id>&device=<device>` adds a `plan` object: the memory preflight for loading that model now (`verdict` `ok` / `warning` / `refuse` / `unknown`, `message`, `replaces`, `memory_estimate_gib`, `combined_estimate_gib`, `available_ram_gib`, `reserve_gib`, `freed_by_replacement_gib`, `resident[]`, and `npu_conflict` when relevant). Unknown models return `404`.

`/health` reports `slots` as `{role: model}` for occupied slots; `/v1/system/status` reports the full array under `models.slots`, and `/v1/models/status` includes it too.

`POST /v1/models/load` accepts two optional fields:

- `slot`: `chat`, `embedding` or `rerank`. It must match the model's role; a mismatch returns `400` instead of being coerced.
- `allow_resource_risk` (default `false`): load even when the preflight refuses.

Loading a model **replaces only the resident model of the same role**; the other slots stay loaded. A device switch of the same model keeps the existing handoff (the old engine serves until the new one compiles). If the model being replaced is busy (serving, queued or switching) or applying a context change, the load returns `409`. Before a model joins *other* resident slots, the combined memory is estimated and the load returns `409` with a friendly `detail` when the new model clearly cannot fit in free RAM (plus what the replaced model frees), or when it would be a second engine on the Intel NPU; set `allow_resource_risk: true` to load anyway. Tight memory or GPU memory only warns (the warning is appended to `message`). The first model on an empty engine keeps the previous single-model behavior. The response adds `slot`, and when a plan was computed `memory` (the plan) and `replaced` (the model id it replaced). In mock mode the NPU rule only warns.

#### Combined devices on load (local extension)

`POST /v1/models/load` also accepts:

- `performance_hint`: `LATENCY`, `THROUGHPUT` or `CUMULATIVE_THROUGHPUT`, for `AUTO` devices only (chat defaults to `LATENCY`).
- `model_distribution`: `pipeline_parallel`, for a chat model on `HETERO` with a GPU and at least one more non-NPU member (sends `MODEL_DISTRIBUTION_POLICY=PIPELINE_PARALLEL`, an OpenVINO preview).

The device (or the server default) is resolved by `app/device_modes.py` before anything compiles. A member that is not available, a hint that does not fit the mode, or a combination classified `unsupported` for the model's backend returns `400` with a friendly `detail`; `allow_resource_risk: true` loads an unsupported combination anyway. `MULTI:<list>` is migrated to `AUTO:<list>` with cumulative throughput (OpenVINO deprecated MULTI). The response adds `device_mode` (`normalized`, `mode`, `members`, `support`, `reason`, `migrated_from`), and a migration notice or the reason for an experimental mode is appended to `message`.

Model entries (`/v1/models/status`, `/v1/system/status`) and slot rows add `execution_devices` (where OpenVINO placed the engine, `null` when unknown; OpenVINO GenAI does not expose it today), `execution_devices_source` (`openvino`, `mock` or `null`), `resolved_device` (for example `AUTO:GPU,CPU → GPU`, otherwise the device) and `combined_device`. A failed combined load sets `error_code: "combined_device_failed"`, offers `load_gpu` or `load_cpu` in `recovery_actions`, and records `device_fallback` (`requested`, `offered`); the device is never switched automatically.

`GET /v1/devices/modes` lists the combined modes worth offering on this PC (`modes[]`: `device`, `mode` (`auto`, `auto_cumulative`, `hetero`), `members`, `kv_cache_precision` (`auto_only`), `may_use_npu`, `backends.{chat,vlm,embedding,rerank}.{support,reason,recommended_hints,options}`, and `cumulative_throughput` for multi-member AUTO), plus `performance_hints`, `migrations`, `execution_devices_reported` and `notes`. In mock mode the list assumes CPU, GPU and NPU.

`POST /v1/models/unload` accepts `{"model": "<id>"}` or `{"slot": "<role>"}` (or both, which must agree: a mismatch returns `400`). `{"slot": ...}` on an empty slot returns `409`. The response adds `slot`.

### `POST /v1/chat/completions`

Supported request fields:

- `model`
- `messages`
- `max_tokens`
- `temperature`
- `top_p`
- `stream`
- `stream_options.include_usage`
- `stop` as a string or array
- `seed`
- `tools`
- `tool_choice`
- `response_format`
- `lora_path`
- `lora_alpha`

Streaming uses Server-Sent Events with `chat.completion.chunk` payloads and terminates with `data: [DONE]`.

Tool calling is implemented with a prompt and parser shim because OpenVINO GenAI does not provide native OpenAI tool-call semantics. Whether a model reliably emits a valid call depends on the model and prompt. Malformed calls receive bounded retry handling.

`response_format` is passed to OpenVINO structured-output support when the installed OpenVINO GenAI version exposes it. Older versions or unsuitable models may accept the request without producing strict JSON. The certification report records that as a warning rather than claiming schema enforcement.

Dynamic LoRA and speculative decoding depend on the installed OpenVINO GenAI version and compatible model artifacts. They are API-supported but require separate real-runtime validation for each adapter or draft-model combination.

### `POST /v1/responses`

Supported request fields:

- `model`
- `input` as text or message-like input
- `instructions`
- `max_output_tokens`
- `temperature`
- `top_p`
- `stream`
- `tools` for local function definitions
- `tool_choice` as `auto`, `none`, `required`, or a specific function
- `parallel_tool_calls`
- `text.format` as `text`, `json_object`, or `json_schema`
- `lora_path`
- `lora_alpha`

InferBridge additionally accepts `stop`, `seed`, and the older `response_format` field as local compatibility extensions so callers can reach capabilities already exposed by the shared OpenVINO generation layer. Use `text.format` for new Responses clients.

Function tools use the Responses-style flat shape:

```json
{
  "type": "function",
  "name": "get_weather",
  "description": "Get current weather.",
  "parameters": {
    "type": "object",
    "properties": {
      "city": {"type": "string"}
    },
    "required": ["city"]
  }
}
```

For compatibility, the Chat Completions nested `function` shape is also accepted. InferBridge returns function calls but does not execute them. Hosted OpenAI tools such as web search, file search, code interpreter, computer use, and MCP are not implemented and are rejected rather than silently ignored.

Tool calling uses the same bounded prompt-and-parser shim as Chat Completions. Malformed function-call JSON receives bounded retry handling. `parallel_tool_calls` is accepted and multiple parsed calls can be returned, but actual parallel execution remains the responsibility of the client.

Structured output is translated into the existing OpenVINO `response_format` generation contract. Strict schema enforcement therefore still depends on the installed OpenVINO GenAI version and model support.

Non-streaming response objects include OpenAI-style token usage with `input_tokens`, `output_tokens`, `total_tokens`, and zero-valued cached/reasoning detail fields when the local runtime does not expose those categories.

Streaming uses Server-Sent Events and can emit:

- `response.created`
- `response.output_item.added`
- `response.content_part.added`
- `response.output_text.delta`
- `response.output_text.done`
- `response.content_part.done`
- `response.function_call_arguments.delta`
- `response.function_call_arguments.done`
- `response.output_item.done`
- `response.completed`
- `error` and `response.failed` for sanitized generation failures
- `data: [DONE]`

The final `response.completed` event contains the completed response object and token usage. Client cancellation closes the underlying model stream so the generation worker can stop and the model lock can be released for the next request.

This route is the recommended compatibility path for n8n workflows that use the Responses API.

### `POST /v1/embeddings`

Supported fields and limits:

- `model`
- `input` as one string or a list of strings
- at most 256 input strings and 2,000,000 combined characters
- empty strings are rejected
- `encoding_format` as `float` or `base64`
- `user` is accepted for client compatibility

The selected model must use the `openvino-embeddings` backend. Text-generation models are rejected, and embedding models are rejected by generation routes.

### `POST /v1/rerank`

Cohere/Jina-compatible document reranking on the resident reranker (backend `openvino-rerank`, OpenVINO GenAI `TextRerankPipeline`). `/rerank` and `/v2/rerank` are aliases with the same authentication and limits.

Request fields and limits:

- `query` (required): 1–32,000 characters, not blank.
- `documents` (required): 1–512 items, each a string or `{"text": "..."}`; empty documents are rejected; at most 100,000 characters per document and 2,000,000 combined.
- `model` (optional): exact reranker model ID. When omitted the model in the `rerank` slot is used; no resident reranker returns `503`.
- `top_n` (optional, ≥ 1): return only the best *n* results.
- `return_documents` (default `true`): include `document.text` in each result.
- `user` is accepted for client compatibility.

Response: `{"id", "object": "rerank", "model", "results": [{"index", "relevance_score", "document"?}], "usage": {"prompt_tokens", "total_tokens"}}`. Results are sorted by `relevance_score` descending (ties by original index); `index` refers to the request's `documents`. For single-logit cross-encoders the score is the sigmoid of the logit (0–1), comparable within one model and query only. Usage counts are estimates. A non-reranker model returns `400` and is equally rejected by generation and embedding routes; an unknown model returns `404`; native scoring failures return a generic `500`. Each query/document pair is truncated to the model's `max_context_len`. See [Reranking](RERANK.md).

## Local management routes

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/v1/huggingface/status` | Sanitized account/source state, no token value. |
| `POST` | `/v1/huggingface/token` | Verify read token against Hugging Face and store in backend (Windows DPAPI). |
| `POST` | `/v1/huggingface/test` | Revalidate currently configured credentials. |
| `DELETE` | `/v1/huggingface/token` | Remove backend-stored token, not environment-owned credentials. |
| `POST` | `/v1/huggingface/preflight` | Check publisher/model access before conversion. |
| `POST` | `/v1/models/register` | Add a custom catalog entry. |
| `GET` | `/v1/models/search-hf` | Search Hugging Face metadata candidates; local hardware compatibility is not guaranteed. |
| `POST` | `/v1/models/download-custom` | Register, convert, and optionally load a custom model. |
| `POST` | `/v1/models/convert` | Convert a catalog model in the background. |
| `GET` | `/v1/models/slots` | Resident model per role, plus an optional memory preflight (`?model=&device=`). |
| `POST` | `/v1/models/load` | Load a converted model on a selected device into its role's slot (`slot`, `allow_resource_risk`, `performance_hint`, `model_distribution`). |
| `POST` | `/v1/models/unload` | Free a loaded engine by `model` or by `slot`. |
| `POST` | `/v1/models/delete` | Delete an unloaded model's local IR directory. |
| `GET` | `/v1/devices` | Return OpenVINO discovery and device suggestions. |
| `GET` | `/v1/devices/modes` | Combined device modes (AUTO, HETERO, cumulative AUTO) classified per backend, with reasons and recommended hints. |
| `GET` | `/v1/system/status` | Return telemetry, lifecycle progress, metrics, and recent safe events. |
| `GET` | `/v1/keys/stats` | Return per-key usage counters without exposing keys. |
| `POST` | `/v1/benchmarks/run` | Benchmark model and device combinations. |
| `GET` | `/v1/benchmarks` | List locally persisted benchmark runs. |
| `GET` | `/v1/benchmarks/latest` | Return the latest run and recommendation. |
| `DELETE` | `/v1/benchmarks` | Clear saved benchmark runs. |
| `POST` | `/v1/chat/export` | Export a supplied conversation as Markdown. |

The Hugging Face search endpoint queries remote metadata only. It does not prove local OpenVINO compatibility, remote-code safety, permission to access a gated model, available disk space, or that a model was downloaded. The backend owns token verification/persistence, gated preflight and model conversion for both frontends. Never log, cache, or display submitted tokens in a frontend.

Native model details use safe optional fields from the same lifecycle snapshot: `backend`, `source_model`, `weight_format`, `recommended_device`, `device`, `max_context_len`, `max_output_tokens` and `supports_vision`. Display validation is allowlist-based: malformed fields are downgraded to *Not reported* rather than echoed or used to authorize cancellation; configured device is a preference, not proven hardware compatibility. A session epoch invalidates in-flight status responses after engine disconnect or API-key challenge.

Native model preparation reads the existing `GET /v1/models/status` schema version `1` (`models.available[].progress.phase/percent/operation_id`, plus `can_cancel`). The client displays only validated stage and bounded percentage; it never renders free-form conversion logs or operation identifiers. `POST /v1/models/cancel` accepts `{model, operation_id}`. The native client confirms the exact same operation still has a cancellation capability in a new status snapshot before submitting, and accepts success only when the backend echoes the model ID, operation ID and `cancelled` state. Because cancellation may complete despite a response timeout, the client never retries automatically; refresh instead. OpenVINO native compilation can be deliberately non-cancellable. No second task queue is introduced.

The `POST /v1/models/download-custom` request uses the existing Pydantic `DownloadCustomRequest` contract: unique filesystem-safe `model_id`, bounded `name`/`source_model`, backend (`openvino-genai`, `openvino-embeddings`, `openvino-vlm`), weight format (`int4`, `int8`, `fp16`), a validated `recommended_device`, context/output token budgets and `load_after`. The native wizard always sends `trust_remote_code=false`, rechecks current device availability and model ID conflicts before POST, and never automatically retries an ambiguous outcome. A `queued_convert` result is an engine **queue acknowledgement**, not completed conversion or verified hardware support. Gated access errors retain the backend's existing `409` behavior. Existing ID collisions must be resolved through the model library, not silent overwrites.

Model conversion and loading are asynchronous. Clients should poll `/v1/system/status` and inspect the matching catalog entry until `is_loaded` is true or an error state is returned.

Custom registration and download requests accept `trust_remote_code`. It defaults to `false`; set it to `true` only for a reviewed Hugging Face repository whose custom Python code is explicitly trusted. `/v1/models/convert` accepts `null` to use the catalog policy or a boolean to override it for that conversion.

## Health routes

- `GET /health` returns process, runtime, device, and model-count state.
- `GET /health/live` is an unauthenticated **loopback** liveness probe.
- `GET /health/ready` returns 503 while model preparation is active and 200 otherwise.

Health routes remain available without an API key to loopback clients so local supervisors can check the process. All non-loopback HTTP access is fail-closed: if no API key is configured, InferBridge rejects the remote request before routing; if an API key is configured, remote UI, asset, and health requests must send the same bearer key. `/v1/*` requests continue through their established route-level authentication so existing throttling and API error behavior are preserved. This keeps accidental wildcard binds and intentional LAN mode consistently authenticated without making localhost probes depend on credentials.

## Authentication, CORS, and rate limiting

Set one or more comma-separated keys with `OV_LLM_API_KEY`. Protected requests must send:

```text
Authorization: Bearer <key>
```

Repeated failed authentication attempts are throttled. Keys are compared using a constant-time comparison and are not returned by usage endpoints.

`OV_LLM_CORS_ORIGINS` is blank by default, so cross-origin browser access is disabled. The bundled UI is served from the API origin and does not need CORS. Configure explicit comma-separated origins for Open WebUI or another browser client. Wildcard CORS is supported for compatibility but should be paired with an API key and avoided when possible.

`OV_LLM_RATE_LIMIT` applies a per-IP requests-per-minute limit when greater than zero. It is a local safety control, not a replacement for a hardened reverse proxy.

## Error behavior

InferBridge uses conventional status codes:

- `400` invalid request, device expression, model or backend pairing, or conversion option
- `401` missing or invalid API key, including authenticated LAN access to non-API surfaces
- `403` non-loopback access attempted while API authentication is disabled
- `404` unknown model
- `409` model is unloaded, busy, loading, or in a conflicting lifecycle state
- `413` request body exceeds the configured maximum
- `422` schema validation failure, including unsupported Responses tool types or tool choices
- `429` configured rate limit or repeated authentication failures
- `500` inference, conversion, deletion, or internal runtime failure
- `503` no model is available or readiness is temporarily blocked

Responses include an `X-Request-ID`. Safe client-supplied IDs are preserved; invalid values are replaced to prevent log injection. Native inference errors are logged with the request ID while client-visible failure bodies remain sanitized.

## Automated validation profiles

```bash
python scripts/validate_api_contract.py --profile core
python scripts/validate_api_contract.py --profile openwebui
python scripts/validate_api_contract.py --profile n8n
python scripts/validate_api_contract.py --profile full
```

The `full` profile covers both external-client request shapes, streaming cancellation, optional embeddings, optional benchmarks, and optional lifecycle exercise. The validator records metadata and assertions only. It does not save prompts or model output.
