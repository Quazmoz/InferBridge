# Desktop and tray troubleshooting

## Tray icon does not appear

Review `%LOCALAPPDATA%\InferBridge\logs\tray.log` or the portable `data\logs\tray.log`. A native error dialog should identify a missing or failed tray dependency. Reinstall a complete artifact rather than installing Python packages manually into the packaged directory.

## Application data is not writable

Move a portable build to a writable local directory or set `OV_LLM_DATA_DIR` to a writable absolute path. Protected application directories and read-only removable media cannot hold models, logs, or diagnostics.

## Server remains in Starting

The tray waits for instance identity, `/health/live`, and `/health/ready`. Review `tray.log` and `desktop.log` for packaged OpenVINO, port, catalog, driver, or model-load failures. The tray uses the configured port when available and otherwise selects a safe loopback fallback.

## Server stopped unexpectedly

The tray enters an Error state and offers Restart. It does not restart forever. Export diagnostics before restarting when the failure is reproducible.

## Stop or Restart takes time

Active generation requests receive bounded drain time. Model conversion, loading, and benchmark operations are not accepted after shutdown begins. If graceful shutdown exceeds its bound, the tray terminates only its validated child process.

## Start with Windows fails

The registration is stored in `HKCU\Software\Microsoft\Windows\CurrentVersion\Run\InferBridge`. No administrator access is required. Portable mode deliberately disables this option. Security software or managed Windows policy may block registry writes; the tray reports the sanitized error.

## Diagnostics export fails

Verify that the writable diagnostics directory is not a symlink and has free space. The collector writes only there, uses a temporary archive, and removes incomplete temporary output after a failure.

## NPU is not shown

The tray displays the actual device reported for the loaded engine. A requested NPU or `AUTO` target is not proof of NPU execution. Use Hardware Scan and the first-run NPU readiness panel, then fall back to an OpenVINO-visible CPU or Intel GPU when necessary.

## Support workflow

1. Reproduce the issue once.
2. Choose **Tray icon → Export Diagnostics**.
3. Review the ZIP contents.
4. Attach the ZIP to a GitHub issue with the steps that triggered the problem.
5. Do not attach models, tokens, certificates, prompts, chat exports, or source images.
