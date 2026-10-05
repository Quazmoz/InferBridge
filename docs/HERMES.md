# Hermes Agent integration

InferBridge exposes an OpenAI-compatible local API that Hermes Agent can use as a
custom/self-hosted provider. The transport contract is compatible: InferBridge exposes
`GET /v1/models` and `POST /v1/chat/completions`, accepts OpenAI-style function tools,
and preserves assistant tool calls plus `tool` result messages in conversation history.

There is an important model-level constraint: **current Hermes Agent requires at least a
64,000-token context window for its main agent model.** InferBridge's bundled catalog is
intentionally configured with much smaller context budgets for practical local CPU/GPU/NPU
operation. Therefore the bundled models are not, by default, Hermes Agent-ready even
though the API endpoint itself is compatible.

Do not work around that check by claiming a 64K context window for a model or InferBridge
configuration that cannot actually serve it. A false value can cause severe truncation,
memory pressure, or failed generation. Use a model whose real context window is at least
64K, register/configure that budget in InferBridge, and qualify it on the target hardware.

Hermes documents custom OpenAI-compatible providers here:
<https://hermes-agent.nousresearch.com/docs/integrations/providers>

## 1. Prepare InferBridge

Load a text-generation model in InferBridge first. In the built-in UI, open
**Generation Settings > Local API > Connection Hub** and copy:

- the active **Base URL**
- the exact loaded **model ID**
- the API-key state

The packaged desktop application can choose a dynamic/fallback port, so prefer the Base
URL shown by Connection Hub rather than assuming port 8000. A source-server default looks
like:

```text
http://127.0.0.1:8000/v1
```

For localhost with InferBridge authentication disabled, a placeholder API key such as
`local-only` is safe for clients that require a non-empty OpenAI API key. If InferBridge
authentication is enabled, use the exact configured InferBridge API key.

Before configuring Hermes, verify model discovery:

```bash
curl http://127.0.0.1:8000/v1/models \
  -H "Authorization: Bearer local-only"
```

Replace the URL and key with the values shown by Connection Hub.

## 2. Verify the context requirement

Hermes Agent currently refuses to start its main agent model below 64,000 tokens. Check
the model's **real** supported context before proceeding.

InferBridge's context-budget UI shows the loaded model's full context window. You can also
inspect the model's configured `max_context_len` in the InferBridge catalog/registration
metadata. For a custom model, set `max_context_len` only to a value the underlying model
and OpenVINO runtime actually support.

A 64K-capable model can still be impractical on an Intel NPU because KV-cache/context
memory and first-load/runtime costs grow with context size. Treat a successful API
connection and successful NPU qualification as separate checks.

## 3. Configure Hermes Agent

The recommended Hermes flow is interactive:

```bash
hermes model
```

Choose **Custom endpoint (self-hosted / VLLM / etc.)** and enter:

```text
API base URL: <InferBridge Base URL ending in /v1>
API key:      <InferBridge key, or local-only for unauthenticated loopback>
Model name:   <exact InferBridge model ID from /v1/models>
Context:      <actual model context; must currently be >= 64000 for Hermes>
```

The equivalent manual configuration is:

```yaml
# ~/.hermes/config.yaml
model:
  default: "<inferbridge-model-id>"
  provider: custom
  base_url: "http://127.0.0.1:8000/v1"
  api_key: "local-only"
  context_length: 65536
```

`65536` above is an example, not a value to copy blindly. Use the model's actual supported
context. If InferBridge authentication is enabled, replace `local-only` with the configured
key and protect the Hermes config file appropriately.

## 4. Validate in layers

Start with plain text before testing agent tools:

1. Confirm `/v1/models` returns the selected model.
2. Start Hermes and send a short prompt.
3. Confirm a normal multi-turn conversation works.
4. Run one simple Hermes tool action.
5. Run a second tool action that requires Hermes to send a prior tool result back to the
   model.
6. Watch InferBridge's context budget and runtime telemetry during a longer session.

InferBridge accepts OpenAI-style `tools` and `tool_choice`, and it accepts the assistant
`tool_calls` / `tool`-result history needed by agent clients. Tool calling is implemented
with a prompt-and-parser compatibility shim because OpenVINO GenAI does not provide native
OpenAI tool-call semantics. Reliability therefore depends heavily on the selected model.
A model being loadable on an Intel NPU does **not** imply that it will be a good Hermes
agent model.

## Troubleshooting

### Hermes reports a context window below 64K

That is a model/configuration compatibility failure, not an OpenAI-API connectivity
failure. Use a model that genuinely supports at least 64K and configure InferBridge with
that real context budget. Do not raise only the Hermes `context_length` value if the
server/model cannot serve it.

### Hermes cannot find the model

Use the exact `id` returned by:

```text
GET <InferBridge Base URL>/models
```

Do not use a display name or Hugging Face repository name unless it is also the exact
InferBridge model ID.

### 401 or 403 errors

For local loopback, confirm you are using the Base URL shown by Connection Hub. For LAN
access, enable InferBridge's authenticated LAN mode and use its generated key. InferBridge
intentionally rejects unauthenticated non-loopback access.

See [LAN and home-lab access](LAN_ACCESS.md) for the network-security model.

### Text works but tools are unreliable

First try a stronger instruction/tool-capable model. InferBridge can transport the tool
schema and tool history, but the local model must still emit parseable calls. If a
specific self-hosted streaming path is problematic, Hermes also supports `stream: false`
as a compatibility escape hatch; use it only after confirming plain non-streaming chat
works.

## Compatibility statement

A precise way to describe the integration is:

> InferBridge can act as Hermes Agent's custom OpenAI-compatible backend. Hermes currently
> requires a 64K-or-larger agent context, so the connection is supported but the selected
> local model and InferBridge context configuration must also meet that requirement.

This distinction matters for Intel Core Ultra/NPU users: the OpenAI-compatible API is the
bridge, while model context size, tool-call quality, OpenVINO compatibility, memory use,
and real NPU execution determine whether a particular model is a practical Hermes setup.
