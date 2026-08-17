# Local Connection Hub

InferBridge's built-in browser UI includes a **Local Connection Hub** under **Generation Settings > Local API**. It is an operational connection surface for OpenAI-compatible local clients, not a public API-management dashboard.

## Connection values

The Hub derives its values from the running server rather than assuming the default port. This matters for the packaged desktop application, which can select an available port at startup.

It shows:

- the active OpenAI-compatible Base URL, including `/v1`
- the configured listener host and active port
- whether API-key authentication is required
- loaded model IDs
- loaded generation-capable model IDs that can be used for chat requests
- whether the server is local-only or configured for LAN access

The configured server API key is never returned by Hub metadata. When authentication is enabled, the Hub shows `YOUR_INFERBRIDGE_API_KEY` as a placeholder. When authentication is disabled, SDK examples use the harmless non-empty value `not-required` for clients that require an API-key string.

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

When API authentication is enabled, the protected self-test coordinator requires the browser session to prove knowledge of the API credential. In packaged desktop mode, a GUI-generated or manually entered browser credential is kept in session-scoped browser storage rather than `localStorage`. The persisted server credential remains in Windows DPAPI storage and is never returned by the Hub.

After the browser proves access, authentication verification runs server-side. InferBridge verifies both a valid configured credential and rejection of an intentionally invalid credential without returning the configured key to browser JavaScript. This prevents an unrelated localhost process from using the Connection Hub as an authenticated inference proxy merely by spoofing the UI marker header.

The Hub also pins its internal callback port to the actual ASGI listener socket, falling back to the configured port only when socket metadata is unavailable. A caller-supplied `Host` port cannot redirect the server-side credential to a different localhost service. Literal loopback hostnames such as `127.0.0.1`, `localhost`, and `::1` remain supported.

Cancellation uses the existing streaming disconnect contract. The self-test opens its own identifiable synthetic stream, receives a valid event, closes only that stream, waits for the generation worker and model lock to release, and then verifies that a small follow-up request succeeds. It does not use the model-preparation cancellation endpoint.

The Connection Hub dialog can be closed while a self-test is running. Closing the dialog does not repurpose the model-preparation cancellation endpoint or attempt to terminate unrelated inference. The small self-test request is allowed to finish, and later checks still skip if normal generation becomes active.

## LAN access

LAN access remains opt-in. The packaged desktop application keeps its loopback-only listener by default.

Packaged users configure LAN mode through **Network / API access** next to the Connection Hub. LAN activation requires an API key, does not automatically enable wildcard CORS, preserves the actual dynamic/fallback port, and shows usable private LAN endpoints after restart.

When a listener is bound beyond loopback, remember that:

- API-key authentication is required for packaged LAN mode
- Windows Firewall and other network controls still determine reachability
- wildcard listeners such as `0.0.0.0` are bind addresses, not client URLs
- remote clients should use the displayed private LAN IP and active port
- InferBridge is not a hardened public internet gateway

InferBridge does not silently configure firewall rules, routers, port forwarding, internet tunnels, or relay services.

See [LAN and home-lab access](LAN_ACCESS.md) for setup, CORS guidance, configuration precedence, and troubleshooting.
