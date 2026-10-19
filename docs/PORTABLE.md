# Portable Windows package

Native-first portable ZIPs contain the self-contained WinUI 3 client in `native\` alongside the packaged Python tray controller, FastAPI server, browser assets, OpenVINO runtime, conversion dependencies, model catalog, and redistribution notices. The older 0.9.6 ZIP did not include WinUI. Model weights are not bundled.

## Use

1. Extract the ZIP to a writable local directory.
2. Run `native\InferBridge.Native.exe` to open the Windows app. `InferBridge.exe` at the ZIP root starts the engine/tray and opens the browser interface instead.
3. Use the native app for model management and chat; the tray also exposes engine and diagnostics operations.
4. Complete first-run model preparation using the native Model library. Advanced features still have a separate web UI. For an older 0.9.6 ZIP, run `InferBridge.exe` and use the browser.

Mutable data is stored under `<portable folder>\data` because the build contains `portable.flag`.

Portable mode does not silently create or permit Start with Windows registration. Install the per-user build for automatic startup.

Avoid protected directories, read-only removable media, and slow network shares. Model conversion requires enough temporary and final disk space.

## Diagnostics and mock validation

```powershell
.\InferBridge.exe --diagnostic --portable
.\InferBridge.exe --mock --headless --headless-seconds 30 --portable --no-browser
```

The diagnostics command writes a sanitized local ZIP. Headless mock mode validates lifecycle contracts but not the native Windows tray backend or real CPU/GPU/NPU execution.
