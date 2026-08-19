# Desktop distribution architecture

The desktop distribution remains a thin controller around the existing FastAPI server and dependency-free browser UI. It does not introduce Electron, Node.js, Docker, cloud inference, or a second model lifecycle system.

## Process model

```text
InferBridge.exe
  └─ tray controller, authoritative per-user owner
       └─ packaged server child
            ├─ existing FastAPI/OpenAI-compatible API
            ├─ existing browser UI
            ├─ existing ModelManager and lifecycle locks
            ├─ existing hardware advisor
            └─ existing benchmark store
```

`app.desktop_launcher` remains the packaged executable entry point and converter/server-child dispatcher. Normal desktop launch delegates to `app.tray_app`.

`app.desktop_controller` owns start, stop, restart, readiness polling, child identity, port selection, metadata, graceful shutdown, and crash detection. It reuses the P0 instance lock, nonce verification, and writable paths.

`app.desktop_operations` presents one typed operational view over existing model-manager, onboarding, hardware-advisor, benchmark, event, and configuration state. Tray status is derived from this view rather than becoming another source of truth.

`app.desktop_network` resolves packaged listener, authentication, CORS, private-LAN endpoint discovery, and secure API-key persistence. `Settings.host` is the authoritative active bind host passed to Uvicorn. Loopback controller URLs remain separate client destinations and are never replaced with `0.0.0.0`.

`app.diagnostics` owns privacy-safe bundle collection independently of tray callbacks so browser and future support/certification tooling can reuse it.

## Lifecycle control boundary

The packaged server binds to `127.0.0.1` by default. Explicit Network / API settings or `OV_LLM_HOST` can request LAN binding. Packaged network exposure is security-gated: without an API key, InferBridge stays on loopback rather than starting an unauthenticated LAN listener.

Public OpenAI-compatible routes keep the existing API-key policy. A GUI-managed packaged key is persisted with Windows DPAPI. A newly generated key can be shown once in Network / API settings so the user can copy it, but the persisted value is not re-exposed by status APIs. When authentication is active, the packaged browser receives a random per-process HttpOnly, SameSite cookie on the loopback UI origin. `app.desktop_browser_auth` accepts that cookie only with a loopback client and loopback Host header, then attaches one configured API key internally to protected browser requests. The stored API key is not placed in page source or browser `localStorage`, and remote LAN clients never receive this browser-auth bridge.

Desktop control routes are excluded from OpenAPI documentation, enforce loopback clients, and require a random per-process `X-Desktop-Control` token. The token is passed from tray to child server and kept in tray memory. It is not returned in status responses or written to normal logs. These control routes remain loopback-only even when the API listener accepts LAN traffic.

Graceful shutdown sets a shutting-down state, rejects new heavyweight model work, allows Uvicorn and ModelManager bounded drain time, cancels managed load/conversion tasks, unloads models, and exits. The tray terminates or kills only its validated child after graceful shutdown exceeds the configured bound.

Browser-initiated restart writes one safe restart marker, asks the server to stop, and lets the authoritative tray restore service once. Network changes use this same restart path so child identity, dynamic port selection, and duplicate-instance protection remain unchanged. Restart failure is surfaced and is not retried forever.

## Listener versus client addresses

A wildcard such as `0.0.0.0` is a bind address, not a client URL. Internal packaged traffic continues to use `127.0.0.1` for:

- instance verification
- liveness and readiness polling
- browser launch
- tray-owned control calls
- graceful shutdown

When LAN mode is active, the Network / API UI derives client endpoints from active RFC1918 IPv4 interfaces and the actual selected port. Discovery failure is non-fatal.

## Single instance and crash recovery

A file lock under the writable data root is held by the tray process. Server metadata is accepted only when its schema is valid and the local `/desktop/instance` nonce matches. Stale metadata is removed after failed live verification.

An unexpected child exit changes tray state to error and makes Restart available. The tray does not automatically restart repeatedly. Only an explicit tray action or one browser restart marker starts another child.

## Installed and portable paths

Installed mode uses `%LOCALAPPDATA%\InferBridge`. Portable mode uses `<portable directory>\data`. Models, configuration, onboarding state, benchmarks, logs, diagnostics, caches, desktop network preferences, and the DPAPI-encrypted desktop API key remain outside packaged resources and survive ordinary upgrades.

See [LAN and home-lab access](LAN_ACCESS.md) for configuration precedence, CORS, endpoint discovery, and firewall guidance.
