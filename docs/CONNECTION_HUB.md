# Local Connection Hub

InferBridge's built-in browser UI includes a **Local Connection Hub** under **Generation Settings > Local API**. It is an operational connection surface for OpenAI-compatible local clients, not a public API-management dashboard.

## Connection values

The Hub derives its values from the running server rather than assuming the default port. This matters for the packaged desktop application, which can select an available loopback port at startup.

It shows:

- the active OpenAI-compatible Base URL, including `/v1`
- the configured listener host and active port
- whether API-key authentication is required
- loaded model IDs
- loaded generation-capable model IDs that can be used for chat requests
- whether the server is local-only or configured for LAN access

The configured API key is never returned to the browser. When authentication is enabled, the Hub shows `YOUR_INFERBRIDGE_API_KEY` as a placeholder. When authentication is disabled, SDK examples use the harmless non-empty value `not-required` for clients that require an API-key string.

If more than one generation-capable model is loaded, select the exact model ID to copy or test. The Hub does not silently choose an arbitrary model.

## Copyable examples

The Quick configuration section provides:

- Base URL
- model ID
- API-key placeholder
- a minimal OpenAI Python SDK example
- a Windows-friendly `curl.exe` model-list request

These generic values are intended for Open WebUI, n8n, scripts, and other applications that support a custom OpenAI-compatible Base URL. Existing product-specific setup notes remain in [External Client Integrations](INTEGRATIONS.md).

## Connection self-test

**Run connection self-test** checks the following independently through InferBridge's HTTP API boundary:

1. Model listing
2. Non-streaming generation
3. Streaming generation
4. Cancellation and a follow-up generation request
5. Authentication behavior

Each check reports `Passed`, `Failed`, or `Skipped` with its own duration and sanitized detail. A skipped generation check is normal when no compatible model is loaded or when the selected model is already busy.

Generation checks use a small synthetic prompt. They do not use conversation history, browser state, files, or other user content. The self-test does not load, unload, convert, delete, or modify models.

Authentication verification runs server-side. If authentication is enabled, InferBridge verifies both a valid server-side credential and rejection of an intentionally invalid credential without sending the configured key to browser JavaScript.

Cancellation uses the existing streaming disconnect contract. The self-test opens its own identifiable synthetic stream, receives a valid event, closes only that stream, waits for the generation worker and model lock to release, and then verifies that a small follow-up request succeeds. It does not use the model-preparation cancellation endpoint.

## LAN access

LAN access remains advanced and opt-in. The packaged desktop application keeps its loopback-only listener by default.

When the configured listener is loopback-only, the Hub reports that other devices cannot connect. When a source server is deliberately bound beyond loopback, the Hub reports that the local network may be able to reach it and reminds you that:

- API-key authentication should be enabled
- Windows Firewall and other network controls still determine reachability
- wildcard listeners such as `0.0.0.0` do not identify one stable LAN address
- InferBridge is not a hardened public internet gateway

The Hub does not enable LAN binding, enumerate private interfaces automatically, change firewall rules, configure routers, forward ports, or provide internet tunneling or relay services.
