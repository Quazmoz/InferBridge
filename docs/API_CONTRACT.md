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

## OpenAI-compatible routes

### `GET /v1/models`

Returns an OpenAI-style model list. InferBridge includes local lifecycle status as an additional field.

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

Supported fields:

- `model`
- `input` as text or message-like input
- `instructions`
- `max_output_tokens`
- `temperature`
- `stream`
- `lora_path`
- `lora_alpha`

Non-streaming responses return an OpenAI-style response object. Streaming emits:

- `response.created`
- `response.output_text.delta`
- `response.output_text.done`
- `response.completed`
- `data: [DONE]`

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

## Local management routes

| Method | Route | Purpose |
|---|---|---|
| `POST` | `/v1/models/register` | Add a custom catalog entry. |
| `GET` | `/v1/models/search-hf` | Search compatible Hugging Face models. |
| `POST` | `/v1/models/download-custom` | Register, convert, and optionally load a custom model. |
| `POST` | `/v1/models/convert` | Convert a catalog model in the background. |
| `POST` | `/v1/models/load` | Load a converted model on a selected device. |
| `POST` | `/v1/models/unload` | Free a loaded engine. |
| `POST` | `/v1/models/delete` | Delete an unloaded model's local IR directory. |
| `GET` | `/v1/devices` | Return OpenVINO discovery and device suggestions. |
| `GET` | `/v1/system/status` | Return telemetry, lifecycle progress, metrics, and recent safe events. |
| `GET` | `/v1/keys/stats` | Return per-key usage counters without exposing keys. |
| `POST` | `/v1/benchmarks/run` | Benchmark model and device combinations. |
| `GET` | `/v1/benchmarks` | List locally persisted benchmark runs. |
| `GET` | `/v1/benchmarks/latest` | Return the latest run and recommendation. |
| `DELETE` | `/v1/benchmarks` | Clear saved benchmark runs. |
| `POST` | `/v1/chat/export` | Export a supplied conversation as Markdown. |

Model conversion and loading are asynchronous. Clients should poll `/v1/system/status` and inspect the matching catalog entry until `is_loaded` is true or an error state is returned.

Custom registration and download requests accept `trust_remote_code`. It defaults to `false`; set it to `true` only for a reviewed Hugging Face repository whose custom Python code is explicitly trusted. `/v1/models/convert` accepts `null` to use the catalog policy or a boolean to override it for that conversion.

## Health routes

- `GET /health` returns process, runtime, device, and model-count state.
- `GET /health/live` is an unauthenticated liveness probe.
- `GET /health/ready` returns 503 while model preparation is active and 200 otherwise.

Health routes remain available without an API key so local supervisors can check the process. `/v1/*` routes are protected when `OV_LLM_API_KEY` is set.

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
- `401` missing or invalid API key
- `404` unknown model
- `409` model is unloaded, busy, loading, or in a conflicting lifecycle state
- `413` request body exceeds the configured maximum
- `429` configured rate limit or repeated authentication failures
- `500` inference, conversion, deletion, or internal runtime failure
- `503` no model is available or readiness is temporarily blocked

Responses include an `X-Request-ID`. Safe client-supplied IDs are preserved; invalid values are replaced to prevent log injection.

## Automated validation profiles

```bash
python scripts/validate_api_contract.py --profile core
python scripts/validate_api_contract.py --profile openwebui
python scripts/validate_api_contract.py --profile n8n
python scripts/validate_api_contract.py --profile full
```

The `full` profile covers both external-client request shapes, streaming cancellation, optional embeddings, optional benchmarks, and optional lifecycle exercise. The validator records metadata and assertions only. It does not save prompts or model output.
