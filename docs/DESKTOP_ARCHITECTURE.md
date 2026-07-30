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

`app.diagnostics` owns privacy-safe bundle collection independently of tray callbacks so browser and future support/certification tooling can reuse it.

## Lifecycle control boundary

The server binds to `127.0.0.1`. Public OpenAI-compatible routes keep the existing optional API-key policy.

Desktop control routes are excluded from OpenAPI documentation, enforce loopback clients, and require a random per-process `X-Desktop-Control` token. The token is passed from tray to child server and kept in tray memory. It is not returned in status responses or written to normal logs.

Graceful shutdown sets a shutting-down state, rejects new heavyweight model work, allows Uvicorn and ModelManager bounded drain time, cancels managed load/conversion tasks, unloads models, and exits. The tray terminates or kills only its validated child after graceful shutdown exceeds the configured bound.

Browser-initiated restart writes one safe restart marker, asks the server to stop, and lets the authoritative tray restore service once. Restart failure is surfaced and is not retried forever.

## Single instance and crash recovery

A file lock under the writable data root is held by the tray process. Server metadata is accepted only when its schema is valid and the local `/desktop/instance` nonce matches. Stale metadata is removed after failed live verification.

An unexpected child exit changes tray state to error and makes Restart available. The tray does not automatically restart repeatedly. Only an explicit tray action or one browser restart marker starts another child.

## Installed and portable paths

Installed mode uses `%LOCALAPPDATA%\InferBridge`. Portable mode uses `<portable directory>\data`. Models, configuration, onboarding state, benchmarks, logs, diagnostics, and caches remain outside packaged resources and survive ordinary upgrades.
