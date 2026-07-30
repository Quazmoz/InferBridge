# Portable Windows package

The portable ZIP contains the packaged tray controller, FastAPI server, browser assets, OpenVINO runtime, conversion dependencies, model catalog, and redistribution notices. Model weights are not bundled.

## Use

1. Extract the ZIP to a writable local directory.
2. Run `InferBridge.exe`.
3. Use the tray icon to open chat, inspect status, manage the owned server, or export diagnostics.
4. Complete first-run model preparation in the existing browser UI.

Mutable data is stored under `<portable folder>\data` because the build contains `portable.flag`.

Portable mode does not silently create or permit Start with Windows registration. Install the per-user build for automatic startup.

Avoid protected directories, read-only removable media, and slow network shares. Model conversion requires enough temporary and final disk space.

## Diagnostics and mock validation

```powershell
.\InferBridge.exe --diagnostic --portable
.\InferBridge.exe --mock --headless --headless-seconds 30 --portable --no-browser
```

The diagnostics command writes a sanitized local ZIP. Headless mock mode validates lifecycle contracts but not the native Windows tray backend or real CPU/GPU/NPU execution.
