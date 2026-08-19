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

Exact model IDs are strict across Chat Completions, Responses, and embeddings. An unknown exact ID returns `404`; InferBridge never silently substitutes the configured default or another loaded engine. The documented `model=auto` and `model=auto:<profile>` advisor selectors remain explicit exceptions that intentionally choose among compatible loaded generation models.

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

Health routes remain available without an API key to **loopback clients** so local supervisors can check the process. If no API key is configured, InferBridge rejects all non-loopback HTTP clients before routing, including health and browser-UI requests. This keeps an accidental `--host 0.0.0.0` or direct Uvicorn wildcard bind from exposing an unauthenticated server. `/v1/*` routes additionally enforce bearer authentication when `OV_LLM_API_KEY` is configured.

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
