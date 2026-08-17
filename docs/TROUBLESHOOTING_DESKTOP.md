# Desktop and tray troubleshooting

## Tray icon does not appear

Review `%LOCALAPPDATA%\InferBridge\logs\tray.log` or the portable `data\logs\tray.log`. A native error dialog should identify a missing or failed tray dependency. Reinstall a complete artifact rather than installing Python packages manually into the packaged directory.

## Application data is not writable

Move a portable build to a writable local directory or set `OV_LLM_DATA_DIR` to a writable absolute path. Protected application directories and read-only removable media cannot hold models, logs, or diagnostics.

## Server remains in Starting

The tray waits for instance identity, `/health/live`, and `/health/ready` through `127.0.0.1`, even when the API listener is enabled for LAN access. Review `tray.log` and `desktop.log` for packaged OpenVINO, port, catalog, driver, model-load, or network-configuration failures. The tray uses the configured port when available and otherwise selects a safe fallback.

## LAN access remains disabled

Open **Generation Settings > Local API > Network / API access** and review the listener, API-key status, and warnings.

Packaged InferBridge will stay on `127.0.0.1` when LAN exposure is requested without an API key. Configure or generate a key, then choose **Apply and restart**.

If `OV_LLM_HOST` is set, it overrides the GUI LAN toggle. If `OV_LLM_CORS_ORIGINS` or `OV_LLM_API_KEY` is set, those environment values also override their desktop-managed counterparts.

Do not enter `0.0.0.0` into a remote client. Use one of the actual private LAN endpoints displayed after restart.

## Another device cannot connect

Verify all of the following:

1. Network / API settings reports **LAN access enabled** after restart.
2. The remote client uses the displayed private IP and actual active port.
3. The client sends the configured API key as a Bearer token.
4. Windows Firewall allows InferBridge on the trusted **Private** network profile.
5. The client is not isolated by guest Wi-Fi, VLAN, VPN, or router policy.

InferBridge does not silently add or broaden Windows Firewall rules. Avoid enabling access on Public profiles.

## Browser client reports CORS errors

CORS is needed only for browser-based clients on a different origin. SDKs, Open WebUI, n8n, and other server-to-server clients normally do not need it.

Add the browser application's exact origin, for example `http://192.168.1.50:3000`. Do not add a URL path. Prefer explicit origins over `*`. Packaged wildcard CORS is security-gated and requires API authentication plus explicit confirmation.

## Server stopped unexpectedly

The tray enters an Error state and offers Restart. It does not restart forever. Export diagnostics before restarting when the failure is reproducible.

## Stop or Restart takes time

Active generation requests receive bounded drain time. Model conversion, loading, and benchmark operations are not accepted after shutdown begins. If graceful shutdown exceeds its bound, the tray terminates only its validated child process.

## Start with Windows fails

The registration is stored in `HKCU\Software\Microsoft\Windows\CurrentVersion\Run\InferBridge`. No administrator access is required. Portable mode deliberately disables this option. Security software or managed Windows policy may block registry writes; the tray reports the sanitized error.

## Diagnostics export fails

Verify that the writable diagnostics directory is not a symlink and has free space. The collector writes only there, uses a temporary archive, and removes incomplete temporary output after a failure. Desktop API keys are not included in diagnostics.

## NPU is not shown

The tray displays the actual device reported for the loaded engine. A requested NPU or `AUTO` target is not proof of NPU execution. Use Hardware Scan and the first-run NPU readiness panel, then fall back to an OpenVINO-visible CPU or Intel GPU when necessary.

## Support workflow

1. Reproduce the issue once.
2. Choose **Tray icon → Export Diagnostics**.
3. Review the ZIP contents.
4. Attach the ZIP to a GitHub issue with the steps that triggered the problem.
5. Do not attach models, tokens, certificates, prompts, chat exports, or source images.

See [LAN and home-lab access](LAN_ACCESS.md) for the full network configuration model.
