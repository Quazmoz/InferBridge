# Quickstart

Windows is the primary target. Ubuntu and Fedora support is experimental.

## Windows

### Requirements

- Windows 11
- Python 3.11, 3.12, 3.13, or 3.14
- Intel CPU
- Intel GPU or NPU only when OpenVINO and the installed drivers expose the device

If `python` or `py` opens the Microsoft Store, disable the Python App execution aliases
in Windows Settings.

### 1. Install

```powershell
git clone https://github.com/Quazmoz/InferBridge.git
cd InferBridge
.\setup.bat
```

Use `.\setup.bat -Minimal` only when you do not need local model conversion.

### 2. Verify the stack in mock mode

```powershell
.\start_server.bat --mock
```

Open `http://127.0.0.1:8000`. Mock mode exercises the API, streaming, lifecycle,
benchmarks, and browser UI without real OpenVINO inference.

### 3. Check real devices

```powershell
.\start_server.bat --check-devices
```

OpenVINO discovery is the source of truth. Start with CPU when GPU or NPU is not listed.

### 4. Start real inference

The shortest first-run flow downloads and converts TinyLlama when needed:

```powershell
.\start_server.bat `
  --model tinyllama-1.1b-chat-fp16 `
  --device CPU `
  --auto-convert
```

For an explicit conversion step:

```powershell
.\setup\convert_model.ps1 -Id tinyllama-1.1b-chat-fp16
.\start_server.bat --model tinyllama-1.1b-chat-fp16 --device CPU
```

Use an NPU only after it appears in device discovery:

```powershell
.\start_server.bat --model tinyllama-1.1b-chat-fp16 --device NPU
```

Gated models require accepted Hugging Face terms and `HF_TOKEN`.

### 5. Certify the Windows hardware path

```powershell
.\scripts\validate_windows.ps1
```

This runs real API, streaming, lifecycle, benchmark, and device checks and creates
sanitized JSON and Markdown reports. Add `-IncludeEmbeddings` for the embedding route.

See [Windows hardware certification](docs/WINDOWS_CERTIFICATION.md).

## Experimental Linux

### Ubuntu prerequisites

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git
```

### Fedora prerequisites

```bash
sudo dnf install -y python3 python3-pip python3-devel git
```

### Setup and run

```bash
git clone https://github.com/Quazmoz/InferBridge.git
cd InferBridge
chmod +x setup.sh start_server.sh setup/*.sh setup/linux/*.sh
./setup.sh --minimal
./start_server.sh --mock
./start_server.sh --check-devices
```

Install conversion dependencies and run CPU inference:

```bash
./setup.sh
./setup/linux/convert_model.sh --id tinyllama-1.1b-chat-fp16
./start_server.sh --model tinyllama-1.1b-chat-fp16 --device CPU
```

Linux GPU and NPU paths remain driver-dependent and experimental.

## Device expressions

Simple targets:

```text
CPU
GPU
NPU
AUTO
```

Advanced examples:

```text
AUTO:NPU,GPU,CPU
AUTO:GPU,NPU,CPU
MULTI:NPU,GPU,CPU
HETERO:NPU,GPU,CPU
```

Advanced routing does not guarantee faster single-request generation. Benchmark the
actual model and machine:

```powershell
python -m app.server `
  --benchmark `
  --benchmark-model tinyllama-1.1b-chat-fp16 `
  --benchmark-devices "CPU;GPU;NPU;AUTO;AUTO:NPU,GPU,CPU"
```

## Useful commands

```powershell
.\start_server.bat --list
.\start_server.bat --check-devices
python scripts\validate_api_contract.py --profile full --expect-real
python -m pytest
ruff check .
ruff format --check .
```

## Fixed port for packaged Windows desktop

Exit the existing InferBridge tray instance before changing ports. Launch the installed executable with `--port 8123` or set `OV_LLM_PORT=8123` in the launching process environment. Explicit ports will not silently fall back if occupied; by default the tray prefers port 8000 and can use a free alternative. Windows user environment changes may require signing out and back in before Start Menu launches inherit them. Do not put `OV_LLM_PORT` in an install-directory `.env` and expect the tray port to change. See [configuration](README.md#fixed-port-for-the-installed-windows-desktop-application).

## Add Qwen3 or import an already-converted model

The full InferBridge catalog has Qwen3 preparation candidates. In **Verified Model Library**, enable **Show all registered** to find `qwen3-4b-int4`, `qwen3-8b-int4`, and the larger `qwen3-30b-a3b-int4`. These are **not** pre-certified on your device; start with CPU and review preflight memory and disk warnings.

To sideload a model already exported to OpenVINO IR, open the browser UI and select **Import local** near the model picker. Enter its absolute directory path and a unique model ID, then select **Import and manage copy**. Raw Hugging Face `.safetensors` weights must first be converted; the import flow does not convert them. See [Qwen3 and sideloading details](docs/MODEL_LIBRARY.md#qwen3-and-sideloading-quickstart-windows).
