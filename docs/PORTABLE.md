# Portable Windows package

Native-first portable ZIPs contain the self-contained WinUI 3 client in `native\` alongside the packaged Python tray controller, FastAPI server, browser assets, OpenVINO runtime, conversion dependencies, model catalog, and redistribution notices. Published releases through 1.0.0 did not include WinUI. Model weights are not bundled.

## Use

1. Extract the ZIP to a writable local directory.
2. Run `native\InferBridge.Native.exe` to open the Windows app. double-clicking the root `InferBridge.exe` also forwards to WinUI in new builds; explicit engine flags such as `--no-browser` still run the backend.
3. Use the native app for model management and chat; the tray also exposes engine and diagnostics operations.
4. Complete first-run model preparation using the native Model library. Advanced features still have a separate web UI. For a published 1.0.0 or earlier ZIP, run `InferBridge.exe` and use the browser.

Mutable data is stored under `<portable folder>\data` because the build contains `portable.flag`.

Portable mode does not silently create or permit Start with Windows registration. Install the per-user build for automatic startup.

Avoid protected directories, read-only removable media, and slow network shares. Model conversion requires enough temporary and final disk space.

## Diagnostics and mock validation

```powershell
.\InferBridge.exe --diagnostic --portable
.\InferBridge.exe --mock --headless --headless-seconds 30 --portable --no-browser
```

The diagnostics command writes a sanitized local ZIP. Headless mock mode validates lifecycle contracts but not the native Windows tray backend or real CPU/GPU/NPU execution.

## Native UI requirement

A native-first portable ZIP contains `native\InferBridge.Native.exe` and its
self-contained runtime. Double-clicking `InferBridge.exe` delegates to native
WinUI; missing native files cause a visible failure rather than silently
opening the browser. Use the explicit native **Open web GUI** command only
when the browser interface is wanted. Engine flags such as `--no-browser`
still select the underlying service/tray behavior for automation.
