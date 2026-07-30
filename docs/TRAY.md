# Windows system tray controller

The installed and portable desktop build uses a lightweight Python system-tray controller as the authoritative owner of the packaged FastAPI server. The existing browser interface remains the primary chat and model-management UI.

## User workflow

1. Launch **InferBridge** from the Start Menu or portable directory.
2. The tray controller acquires the per-user application lock.
3. It starts the packaged server on `127.0.0.1`, waits for liveness and readiness, and optionally opens the browser.
4. Closing the browser does not stop the server or tray controller.
5. Use the tray menu to inspect status, manage the owned server, copy connection settings, run local checks, or export diagnostics.

## Menu

The root menu is intentionally small. Related actions are grouped into Status, Copy, Server, and Folders submenus.

- **Open Chat** opens the existing browser UI on the actual active port.
- **Status** displays server state, active model, actual device, and model-preparation progress.
- **Copy** provides the API base URL, chat URL, and OpenAI-compatible environment/Python configuration. It uses a placeholder instead of copying a configured API key.
- **Server** starts, stops, or restarts the owned server, refreshes the hardware scan, and runs the existing short benchmark when a generation model is loaded.
- **Folders** opens the writable model and log directories.
- **Export Diagnostics** creates a local sanitized ZIP after a confirmation summary.
- **Start with Windows** manages one per-user startup registration in `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`.
- **About** displays version and privacy information.
- **Quit** gracefully stops only the server child owned by the tray, then exits the tray process.

Menu items are disabled when their operations are unsafe or unavailable. Start is disabled while the server is running. Stop and Restart are disabled while stopped. Benchmark is disabled without a loaded generation model or during active model preparation.

## State and icon behavior

The tray has distinct glyphs as well as colors for starting, ready, preparing a model, warning, error, stopped, and unknown.

The tooltip includes concise status, model, and device information. An NPU is shown only when the loaded engine status reports NPU as the actual device.

## Process ownership

The tray keeps the P0 per-user lock for its entire lifetime. Server metadata contains a validated PID, port, executable, random instance nonce, and start timestamp. A PID alone is never trusted.

The tray stores a separate random server-control token only in process memory and passes it to the child server. Loopback-only control routes require that token. The token is not returned by status endpoints and is not written to normal logs.

A second launcher invocation opens the existing healthy browser UI. When the tray is running with the server stopped, the second invocation writes a bounded local activation command that asks the existing tray to start the server. It does not start a competing owner.

The tray terminates only the `subprocess.Popen` child it created and whose PID matches current validated metadata. It never searches for or kills unrelated Python processes.

## Start with Windows

Start with Windows is disabled by default. Enabling it creates one per-user registry value:

```text
HKCU\Software\Microsoft\Windows\CurrentVersion\Run\InferBridge
```

The command starts the packaged tray controller with `--startup --no-browser`. Windows startup therefore restores the tray and server in the background without opening the browser.

Disabling the option removes that value. Duplicate registrations are not created. Portable mode does not permit automatic startup registration; install the application per-user first.

## Mock validation

Automated tests can run the tray-owned lifecycle without a graphical tray backend by using:

```powershell
InferBridge.exe --mock --headless --headless-seconds 30 --no-browser
```

Headless mode is intended for packaging and integration tests. It does not validate the native Windows notification-area backend.
