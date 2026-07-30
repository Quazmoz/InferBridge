# Quickstart

Windows is the primary target. Ubuntu and Fedora support is experimental.

## Windows

### Requirements

- Windows 11
- Python 3.11, 3.12, or 3.13
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
