# InferBridge and Open WebUI

InferBridge exposes an OpenAI-compatible `/v1` API that Open WebUI can use as a local model provider.

## Correct connection type

Use an **OpenAI API** or **OpenAI-compatible** connection in Open WebUI.

Do not configure the InferBridge URL as an Ollama API. InferBridge does not implement Ollama endpoints such as `/api/tags` or `/api/chat`. Configuring the same URL under both connection types can produce confusing model behavior.

Recommended local setup:

```text
OpenAI API: enabled
Ollama API: disabled for this URL
Base URL:   http://127.0.0.1:8000/v1
API key:    sk-dummy unless OV_LLM_API_KEY is set
```

Depending on the Open WebUI version, connection settings may appear under **Settings > Connections** or **Admin Settings > Connections**.

## Start InferBridge

Open WebUI discovers selectable models through:

```text
GET /v1/models
```

InferBridge must be running, and a converted text-generation model should be loaded before starting a chat. Open WebUI is a client. It does not automatically call InferBridge conversion or lifecycle endpoints.

Start with a converted model loaded:

```powershell
# Intel NPU when the model, driver, and OpenVINO runtime support it
.\start_server.bat --model tinyllama-1.1b-chat-fp16 --device NPU

# Conservative fallback while validating hardware or conversion
.\start_server.bat --model tinyllama-1.1b-chat-fp16 --device CPU

# Contract smoke testing without real OpenVINO inference
.\start_server.bat --mock --model tinyllama-1.1b-chat-fp16
```

If InferBridge starts without `--model`, open the built-in UI at `http://127.0.0.1:8000` and convert or load a model first. You can also call the lifecycle API directly:

```powershell
curl -X POST http://127.0.0.1:8000/v1/models/load `
  -H "Content-Type: application/json" `
  -d '{"model":"tinyllama-1.1b-chat-fp16","device":"CPU"}'
```

## Configure Open WebUI

1. Open **Settings** or **Admin Settings**.
2. Open **Connections**.
3. Add or edit an **OpenAI API** connection.
4. Set the base URL to `http://127.0.0.1:8000/v1`.
5. Use any non-empty placeholder key when InferBridge authentication is disabled.
6. When `OV_LLM_API_KEY` is configured, enter that exact key in Open WebUI.
7. Save the connection and refresh the model list.

If Open WebUI shows stale models, temporarily disable **Cache Base Model List**, save the connection again, and refresh the page.

## Multiple models

Open WebUI can select only models returned by `/v1/models`. Load each model you want to expose before refreshing Open WebUI.

```powershell
curl -X POST http://127.0.0.1:8000/v1/models/load `
  -H "Content-Type: application/json" `
  -d '{"model":"tinyllama-1.1b-chat-fp16","device":"CPU"}'

curl -X POST http://127.0.0.1:8000/v1/models/load `
  -H "Content-Type: application/json" `
  -d '{"model":"qwen2.5-0.5b-fp16","device":"CPU"}'

curl http://127.0.0.1:8000/v1/models
```

Loading multiple models consumes additional memory and device resources. InferBridge warns when another model is already loaded. On constrained hardware, unload the current model before loading another one.

## Required compatibility endpoints

Open WebUI primarily uses:

```text
GET  /v1/models
POST /v1/chat/completions
```

InferBridge supports streaming and non-streaming chat completions:

```json
{
  "model": "tinyllama-1.1b-chat-fp16",
  "messages": [
    {"role": "user", "content": "What is 2+2?"}
  ],
  "stream": true
}
```

Streaming responses use OpenAI-style Server-Sent Events and end with:

```text
data: [DONE]
```

## Text-to-speech note

InferBridge is an LLM and VLM inference server. It does not currently implement an OpenAI-compatible `/v1/audio/speech` endpoint.

Configure Kokoro or another text-to-speech engine as a separate audio provider in Open WebUI:

```text
LLM chat:    Open WebUI -> InferBridge -> /v1/chat/completions
Voice / TTS: Open WebUI -> separate TTS service -> audio output
```

The legacy `npu-windows` project is a separate historical server and is not part of the InferBridge runtime.

## Quick compatibility test

Run the included PowerShell check:

```powershell
.\scripts\test_openwebui_compat.ps1 -BaseUrl http://127.0.0.1:8000/v1
```

Specify a model and key when needed:

```powershell
.\scripts\test_openwebui_compat.ps1 `
  -BaseUrl http://127.0.0.1:8000/v1 `
  -Model tinyllama-1.1b-chat-fp16 `
  -ApiKey sk-dummy
```

The check verifies:

- `/v1/models` returns an OpenAI-style model list
- the selected model can answer `/v1/chat/completions`
- streaming responses emit `data:` chunks and terminate with `data: [DONE]`

## LAN or container access

`localhost` inside an Open WebUI container refers to the container, not the Windows host. Use the host gateway or the Windows machine's private IP address.

Example:

```text
http://192.168.1.50:8000/v1
```

To bind InferBridge beyond loopback:

```powershell
$env:OV_LLM_API_KEY = "replace-with-a-local-secret"
$env:OV_LLM_CORS_ORIGINS = "http://openwebui-host:3000"
.\start_server.bat --host 0.0.0.0 --port 8000 --model tinyllama-1.1b-chat-fp16 --device CPU
```

Use only a trusted private network. Restrict Windows Firewall to the intended subnet and port. InferBridge is not a hardened public internet gateway.

## Troubleshooting

### No models appear

- Confirm InferBridge is running.
- Confirm at least one model is loaded.
- Open `http://127.0.0.1:8000/v1/models` directly.
- Refresh the OpenAI-compatible connection.
- Temporarily disable Open WebUI's model-list cache.

### Chat fails after model discovery

Test InferBridge directly:

```powershell
curl http://127.0.0.1:8000/v1/models

curl -X POST http://127.0.0.1:8000/v1/chat/completions `
  -H "Content-Type: application/json" `
  -d '{"model":"tinyllama-1.1b-chat-fp16","messages":[{"role":"user","content":"What is 2+2?"}],"stream":false}'
```

When authentication is enabled, include `Authorization: Bearer <OV_LLM_API_KEY>`.

### Only one model appears

Confirm `/v1/models` returns multiple loaded models. If it does, refresh the OpenAI connection and clear the cached base model list in Open WebUI.

### Connection works locally but not from a container

Use the Windows host gateway or private IP instead of `localhost`. Confirm the bind address, API key, CORS configuration, and Windows Firewall rule.
