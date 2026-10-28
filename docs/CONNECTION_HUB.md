# Local Connection Hub

InferBridge's built-in browser UI includes a **Local Connection Hub** under **Generation Settings > Local API**. It is an operational connection surface for OpenAI-compatible local clients, not a public API-management dashboard.

The native WinUI app now has a dedicated **Local API** navigation page. It reads
the same `/internal/connection-hub` metadata and runs the same backend-owned
`/internal/connection-hub/self-test`. Native requests use the currently verified
loopback engine session, the local UI marker and an optional session-only API key.
The native client rechecks the engine nonce before displaying returned metadata
and after the synthetic self-test, rejecting results from a replaced session.
It does not reconfigure listeners or retrieve DPAPI-managed credentials.

## Connection values

The Hub derives its values from the running server rather than assuming the default port. This matters for the packaged desktop application, which can select an available port at startup.

It shows:

- the active OpenAI-compatible Base URL, including `/v1`
- the configured listener host and active port
- whether API-key authentication is required
- loaded model IDs
- loaded generation-capable model IDs that can be used for chat requests
- each loaded generation model's configured `context_tokens` and `max_output_tokens` (null when unknown), used by **Connect a tool**
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

## Connect a tool

Both the native **Local API** page and the browser Connection Hub include a **Connect a tool** section with one card per client. Every value on a card comes from the running engine (Base URL with the active port, authentication state, the selected loaded chat model and its configured context window and output budget); nothing assumes the default port. A card shows **Ready** only when the engine is available and a loaded generation-capable model is selected.

Each card has the exact values with copy buttons, numbered steps, a link to the client's official documentation, a **Test connection** button that runs the same engine-owned self-test described below, and an honest note that agentic coding tools need tool calling and a large context window. The note flags a context window that is unknown, small (under 32k tokens) or below the 128k tokens GitHub recommends for Copilot CLI.

| Client | Setup | Mechanism (verified October 2026) |
| --- | --- | --- |
| Continue (VS Code, JetBrains) | One-click in the native app | Merges one model named `InferBridge (local)` (`provider: openai`, `apiBase`, `model`, `apiKey`, `defaultCompletionOptions.contextLength`/`maxTokens` when known) into `%USERPROFILE%\.continue\config.yaml` ([config.yaml reference](https://docs.continue.dev/reference), [file location](https://docs.continue.dev/guides/understanding-configs)) |
| GitHub Copilot CLI | One-click in the native app (installer/portable editions) | Sets user environment variables `COPILOT_PROVIDER_BASE_URL`, `COPILOT_PROVIDER_TYPE=openai`, `COPILOT_MODEL`, and `COPILOT_PROVIDER_MAX_PROMPT_TOKENS`/`COPILOT_PROVIDER_MAX_OUTPUT_TOKENS` when known ([GitHub Docs](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/use-byok-models)) |
| VS Code GitHub Copilot Chat | Guided | **Chat: Manage Language Models → Add Models → Custom Endpoint**, with a copyable `chatLanguageModels.json` entry; a separate snippet for the deprecated `github.copilot.chat.customOAIModels` setting is labelled deprecated ([VS Code docs](https://code.visualstudio.com/docs/agent-customization/language-models)) |
| GitHub Copilot app | Guided | **Settings → Model providers → Add provider → OpenAI-compatible endpoint** (public preview) ([GitHub Docs](https://docs.github.com/en/copilot/how-tos/github-copilot-app/use-byok-models)) |
| Cline | Guided | API Provider **OpenAI Compatible**: Base URL, API key, Model ID, Model Configuration ([Cline docs](https://docs.cline.bot/provider-config/openai-compatible)) |
| Roo Code / Zoo Code | Guided | Roo Code was shut down on 15 May 2026 ([Roo Code README](https://github.com/RooCodeInc/Roo-Code)); its community fork Zoo Code keeps the same **OpenAI Compatible** provider ([Zoo Code docs](https://github.com/Zoo-Code-Org/Zoo-Code-Docs/blob/main/docs/providers/openai-compatible.md)) |
| Open WebUI, n8n, scripts | Guided | Generic Base URL, model ID and key placeholder |

### One-click safety rules

One-click setup exists only in the native Windows app, because the browser cannot and must not write local files or environment variables.

- Nothing is written without an explicit click: **Preview change…** shows the exact entry or the before/after value of every variable, and the confirmation dialog defaults to **Cancel**.
- **Continue:** the file is backed up next to itself (`config.yaml.inferbridge-<timestamp>.bak`) before any write. The merge keeps every other model and setting, updates only the entry between InferBridge's own comment markers, and is idempotent. A file InferBridge cannot merge safely (tab indentation, inline/flow `models` list, duplicate `models` keys, several YAML documents, a hand-written `InferBridge (local)` entry, or anything that is not a top-level mapping) is refused and left byte-for-byte unchanged. The write is refused if the file changed after the preview. **Undo** restores the backup (or removes a file InferBridge created) only if the file still matches what InferBridge wrote; otherwise it reports the backup path and leaves the user's edits alone.
- **Copilot CLI:** prior values are recorded and **Undo** puts them back, skipping any variable the user changed afterwards. A partial failure rolls back what was already set. Open a new terminal afterwards. In the **Microsoft Store edition**, Windows virtualizes registry writes under `HKCU` for packaged desktop apps ([Microsoft Learn](https://learn.microsoft.com/en-us/windows/msix/desktop/desktop-to-uwp-behind-the-scenes)), so variables written by the app would never reach the user's terminals. The Store card therefore offers copyable PowerShell commands instead of a one-click button.
- **API keys:** no real key is written to `config.yaml`, environment variables or snippets. Cards use `YOUR_INFERBRIDGE_API_KEY` when authentication is on and `not-required` when it is off. The VS Code snippet uses `${input:inferbridgeApiKey}` so VS Code keeps the key in its secret storage. On the native page, the API key **Copy** button puts the session key typed on **Engine & devices** on the clipboard (never saved); otherwise it copies the placeholder. For Copilot CLI with authentication on, set `COPILOT_PROVIDER_API_KEY` yourself in the terminal session.
- Undo is available for the current app session. The Continue backup file stays on disk for manual recovery.

## Connection self-test

**Run connection self-test** checks the following independently through InferBridge's HTTP API boundary:

1. Model listing
2. Non-streaming generation
3. Streaming generation
4. Cancellation and a follow-up generation request
5. Authentication behavior

Each check reports `Passed`, `Failed`, or `Skipped` with its own duration and sanitized detail. A skipped generation check is normal when no compatible model is loaded or when the selected model is already busy.

The native page reports every check independently in an accessible result list.
If the HTTP response is lost or the window closes, the result is *unknown*;
the client does not automatically retry generation. Copy buttons put only the
Base URL, selected model example, or placeholder-bearing snippet on the clipboard.

Generation checks use a small synthetic prompt. They do not use conversation history, browser state, files, or other user content. The self-test does not load, unload, convert, delete, or modify models.

When API authentication is enabled in packaged desktop mode, the local browser receives a random per-process HttpOnly cookie that proves the request belongs to the loopback InferBridge UI session. The desktop server uses that proof to attach one configured API credential internally to protected `/v1/*` requests and the Connection Hub self-test. The persisted API key remains in DPAPI or the process environment and is never returned by Hub metadata or stored in browser `localStorage`. A key generated from Network / API settings is shown once so the user can copy it before the stored value becomes non-retrievable through the UI.

Remote LAN clients do not receive this local-browser bridge. They must present their configured API key normally. Loopback SDK or CLI clients without the desktop UI cookie also keep the ordinary explicit API-key contract.

Authentication verification inside the Hub still runs server-side. InferBridge verifies both a valid configured credential and rejection of an intentionally invalid credential without returning the persisted key to browser JavaScript. The local-browser bridge is restricted to a matching loopback client and loopback Host header, and it does not authenticate remote LAN requests.

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
