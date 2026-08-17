# External Client Integrations

The server is designed for OpenAI-compatible clients. CI validates the HTTP request and
streaming contracts against the mock engine. Real client and hardware validation should
be recorded separately because client versions, Windows drivers, and OpenVINO packages
can change behavior.

For the values from the server that is running now, open **Generation Settings > Local
API > Connection Hub** in the built-in UI. The Hub shows the active Base URL, loaded
model IDs, authentication state, generic copyable examples, and an independent API
self-test. Packaged desktop users can configure authenticated LAN access under **Network /
API access**. See [Local Connection Hub](CONNECTION_HUB.md) and [LAN and home-lab
access](LAN_ACCESS.md). The examples below remain reference configuration; packaged
desktop launches may use a different active port.

## Packaged desktop LAN setup

For the packaged application, prefer the built-in UI instead of Windows environment
variables:

1. Open **Generation Settings > Local API > Network / API access**.
2. Generate or enter a strong API key.
3. Enable **Allow access from other devices on my local network**.
4. Leave CORS blank unless the client is a browser application on another origin.
5. Choose **Apply and restart**.
6. Use one of the displayed private LAN Base URLs from the remote client.

Do not use `0.0.0.0` as a client URL. It is only a bind address. InferBridge preserves the
actual dynamic/fallback port selected by the desktop launcher.

## Open WebUI

### Server setup

Start a loaded text-generation model:

```powershell
.\start_server.bat `
  --model tinyllama-1.1b-chat-fp16 `
  --device CPU `
  --auto-convert
```

For a trusted LAN connection in source mode, bind deliberately and require a key:

```powershell
$env:OV_LLM_API_KEY = "replace-with-a-local-secret"
.\start_server.bat --host 0.0.0.0 --model tinyllama-1.1b-chat-fp16 --device CPU
```

Open WebUI is normally a server-to-server client, so CORS is not required merely because
it runs on another machine. Configure `OV_LLM_CORS_ORIGINS` only when a browser origin
actually calls InferBridge directly.

Do not expose this server directly to the internet.

### Open WebUI connection

Use an OpenAI-compatible connection. Prefer the actual Base URL shown by Connection Hub
or Network / API settings. The default source-server shape is:

```text
Base URL: http://127.0.0.1:8000/v1
API key:  any non-empty value when auth is disabled
API key:  the exact OV_LLM_API_KEY value when auth is enabled
```

From another trusted machine, replace `127.0.0.1` with the Windows host's displayed
private IP. Ensure Windows Firewall permits InferBridge only on the intended Private
network profile and active port.

### Validate the Open WebUI request contract

```powershell
python .\scripts\validate_api_contract.py `
  --profile openwebui `
  --model tinyllama-1.1b-chat-fp16 `
  --device CPU `
  --expect-real
```

This validates model listing, model load, non-streaming chat, streaming SSE, usage
chunks, cancellation recovery, tool-call request acceptance, structured-output request
acceptance, and metrics.

### Manual client checklist

After the contract passes, verify the actual Open WebUI version:

1. The model appears in the model selector.
2. A non-streaming response completes.
3. A streaming response renders incrementally.
4. Stopping generation does not leave the next request blocked.
5. Long conversations receive a safe context-budget response rather than crashing.
6. API-key failures are visible without exposing the key.
7. Tool calls are treated as model-dependent and are not assumed to be native.

Record the Open WebUI version in any published compatibility note.

## n8n

### Recommended endpoint

Use the Base URL shown by Connection Hub or Network / API settings. For a source server
on the default port, that is:

```text
http://127.0.0.1:8000/v1
```

The server supports both Chat Completions and the Responses API. For newer n8n nodes
that use Responses, `/v1/responses` supports streaming and non-streaming text output.

n8n is normally a server-to-server client and does not require CORS for a normal OpenAI-
compatible connection.

### Validate the n8n request contract

```powershell
python .\scripts\validate_api_contract.py `
  --profile n8n `
  --model tinyllama-1.1b-chat-fp16 `
  --device CPU `
  --expect-real
```

This validates model discovery plus the non-streaming and streaming Responses event
sequence expected by OpenAI-compatible automation clients.

### Manual workflow checklist

1. Configure an OpenAI-compatible credential with the local or LAN base URL.
2. Select the loaded catalog model ID exactly as returned by `/v1/models`.
3. Run one simple text workflow without streaming.
4. Run a streaming-capable workflow when supported by the selected node.
5. Confirm errors surface as workflow errors rather than malformed successful output.
6. Confirm retries do not duplicate external side effects in downstream workflow steps.
7. Keep model lifecycle operations outside concurrent production workflow runs.

Record the n8n version and node type in any published compatibility note.

## Direct SDK and agent clients

Any client that allows a custom OpenAI base URL can start with the active values from
Connection Hub. A default source-server example is:

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8000/v1",
    api_key="local-only",
)

response = client.chat.completions.create(
    model="tinyllama-1.1b-chat-fp16",
    messages=[{"role": "user", "content": "Hello from a local client"}],
)
print(response.choices[0].message.content)
```

For a LAN client, use the displayed private LAN Base URL and configured API key. Ordinary
SDK clients do not need CORS.

The official OpenAI SDK is not required by the server. This example is for external
client usage only.

## Validation boundaries

The automated profiles prove HTTP and streaming compatibility with the implemented
contract. They do not prove:

- quality of a selected model
- native tool calling by OpenVINO GenAI
- compatibility of arbitrary LoRA adapters
- compatibility of arbitrary speculative-decoding model pairs
- every Open WebUI or n8n release
- GPU or NPU support that was not exercised on real hardware
- Windows Firewall behavior on an untested machine or network profile

Use the Windows certification report for hardware claims and the manual checklist for
actual client-version claims.
