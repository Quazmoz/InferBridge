# Local vision chat

InferBridge can serve vision-language models through the same local,
OpenAI-compatible API used for text chat. Image bytes remain local and are held in
memory only for the active request.

## Supported input

- JPEG, PNG, and WebP
- up to 4 images per request
- up to 10 MiB per image
- up to 24 MiB combined image data
- up to 25,000,000 decoded pixels per image
- up to 40,000,000 decoded pixels across the request
- maximum dimension of 16,384 pixels per side
- static images only; animated images are rejected
- base64 `data:` URLs only
- up to 1,024 content parts and 2,000,000 text characters per request

Remote `http://` and `https://` image URLs are intentionally rejected. The server does
not fetch user-controlled URLs, which avoids turning a LAN-bound installation into an
SSRF-capable proxy.

## Browser interface

Select a model registered with the `openvino-vlm` backend. The composer then enables an
image button and supports:

- file selection
- drag and drop
- pasting a screenshot from the clipboard
- attachment previews and removal

Attachments are sent with the next request only. Successful non-streaming requests clear
the sent attachments immediately. Streaming requests clear them only after a complete,
error-free SSE stream; failed or interrupted requests retain them for retry. The browser
conversation store keeps the text message but does not retain base64 image data.

## Register and convert a VLM

Use **Add custom model** in the browser and select **Vision + Text
(openvino-vlm)**, or call the API:

```powershell
$body = @{
  model_id = "local-vision-int4"
  name = "Local Vision INT4"
  source_model = "<trusted Hugging Face VLM repository>"
  backend = "openvino-vlm"
  weight_format = "int4"
  recommended_device = "GPU"
  max_context_len = 4096
  max_output_tokens = 512
  load_after = $true
  trust_remote_code = $false
} | ConvertTo-Json

Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/v1/models/download-custom `
  -ContentType application/json `
  -Body $body
```

The converter selects Optimum's `image-text-to-text` task for `openvino-vlm`
registrations. Hugging Face remote code execution is disabled by default. Set
`trust_remote_code` to `true`, or enable the warning checkbox in the browser, only after
reviewing and trusting the repository because conversion may then execute its Python
code. The setting is persisted in `models.json` for explicit subsequent conversions.

Model architecture, OpenVINO GenAI version, driver, precision, and target device must
still be compatible. Start with CPU or GPU unless the chosen VLM explicitly supports
your NPU path.

## Chat Completions API

```python
import base64
from pathlib import Path

import httpx

image = base64.b64encode(Path("screenshot.png").read_bytes()).decode("ascii")
payload = {
    "model": "local-vision-int4",
    "messages": [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Describe this screenshot and identify the error."},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{image}"},
                },
            ],
        }
    ],
    "stream": False,
}

response = httpx.post(
    "http://127.0.0.1:8000/v1/chat/completions",
    json=payload,
    timeout=120,
)
response.raise_for_status()
print(response.json()["choices"][0]["message"]["content"])
```

Streaming uses the same request shape with `"stream": true`.

## Responses API

```json
{
  "model": "local-vision-int4",
  "input": [
    {
      "role": "user",
      "content": [
        {"type": "input_text", "text": "What is shown in this image?"},
        {
          "type": "input_image",
          "image_url": "data:image/png;base64,<BASE64_DATA>"
        }
      ]
    }
  ]
}
```

## Runtime behavior

The HTTP layer rejects oversized bodies before JSON parsing. The default limit is 40 MiB
and can be changed with `OV_LLM_MAX_REQUEST_BODY_MB` or
`--max-request-body-mb`. Keep it above the 24 MiB decoded-image limit because base64 adds
encoding overhead.

The request parser performs a lightweight request-wide preflight before decoding. During
prompt construction, each image is base64-decoded and fully verified once, then its
validated bytes are carried as an immutable request-local payload. Tool-call retries reuse
those payloads rather than decoding the source data again. A VLM engine replaces typed
image parts with OpenVINO
GenAI image tags and supplies oriented, contiguous `ov.Tensor` images to `VLMPipeline`.
Bounded temporary image contexts are consumed once and released when prompt-budget
candidates are discarded, a request fails, or a stream closes.

Text-only models never receive base64 image bytes. The browser disables attachments for
those models, and the API rejects image input for a text-only model with HTTP 400 rather
than silently discarding it. Vision requests reserve prompt capacity for image embeddings;
requests that leave no generation room are rejected explicitly.

## Validation status

Mock mode exercises image validation, OpenAI content-part parsing, browser request
construction, prompt budgeting, lifecycle routing, streaming, and cancellation without
claiming real hardware support. Publish a real Windows CPU, GPU, or NPU compatibility
result only after the repository's hardware certification workflow succeeds with the
specific VLM and device.
