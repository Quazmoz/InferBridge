# Experimental Linux Support

Linux support is experimental and currently supports Ubuntu and Fedora. InferBridge is still Windows-first, so start with CPU validation before spending time on GPU/NPU driver work.

## Choose Your Distro

- [Ubuntu setup](UBUNTU.md): Ubuntu 22.04 or 24.04.
- [Fedora setup](FEDORA.md): Fedora 40 or newer.

## Common Commands

```bash
./setup.sh --minimal
./start_server.sh --mock
./start_server.sh --check-devices
./start_server.sh --model tinyllama-1.1b-chat-fp16 --device CPU
```

Use the platform-owned setup helpers for direct calls:

```bash
./setup/linux/install_deps.sh --minimal
./setup/linux/check_hardware.sh
./setup/linux/convert_model.sh --id tinyllama-1.1b-chat-fp16
```

The older `setup/*.sh` paths remain compatibility wrappers.

## Linux desktop controller

The browser UI remains the primary Linux experience, but the desktop launcher can now be used from a source checkout:

```bash
./.venv/bin/python -m app.desktop_launcher --mock
```

If the desktop environment supports a compatible tray backend, InferBridge exposes the tray controls. If a tray backend is missing or fails to initialize, the launcher keeps the local server running instead of terminating it. The browser UI remains available at the local server URL.

Linux desktop data follows the XDG Base Directory convention:

- `$XDG_DATA_HOME/InferBridge` when `XDG_DATA_HOME` is an absolute path.
- `~/.local/share/InferBridge` otherwise.
- `OV_LLM_DATA_DIR` remains an explicit override.
- Portable mode continues to use the launcher's local `data/` directory.

Folder opening uses `xdg-open` with a `gio open` fallback. Clipboard actions support `wl-copy` on Wayland and `xclip`/`xsel` on X11 when those utilities are installed. Dialogs use `zenity` or `kdialog` when available and otherwise fall back to terminal/log output.

## Hugging Face credentials

Linux setup never copies a Hugging Face CLI token into the repository `.env` file. For gated-model access, accept the model terms and export the token in the environment used to start InferBridge:

```bash
export HF_TOKEN=hf_...
./start_server.sh --model <model-id> --device CPU
```

This keeps the token user-controlled rather than duplicating it into the checkout.

## Accelerator diagnostics

`./setup/linux/check_hardware.sh` reports:

- OpenVINO-visible CPU/GPU/NPU devices and driver versions when available.
- `/dev/dri/renderD*` access for Intel GPU execution.
- `/dev/accel/accel*` access for Intel NPU execution.
- `render`/`video` group membership and relevant kernel-module hints.

PCI visibility is only a hint. InferBridge only targets GPU or NPU when OpenVINO actually exposes that device.
