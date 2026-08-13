# Experimental Fedora support

Linux support is experimental and currently supports Ubuntu and Fedora. This page describes Fedora-specific setup; see [UBUNTU.md](UBUNTU.md) for Ubuntu.

InferBridge remains Windows-first. Fedora support is a CPU-first path for developers who want to try the Python, FastAPI, and OpenVINO stack while treating GPU and NPU execution as driver-dependent experiments.

## Expected baseline

- Fedora 40 or newer is expected.
- Python 3.11, 3.12, 3.13, or 3.14 is expected.
- CPU inference is the recommended first path.
- GPU or NPU execution requires compatible Intel hardware and Linux drivers.
- Fedora GPU and NPU support is experimental and hardware-dependent.

## Install

```bash
sudo dnf install -y python3 python3-pip python3-devel git

git clone https://github.com/Quazmoz/InferBridge.git
cd InferBridge
chmod +x setup.sh start_server.sh setup/*.sh setup/linux/*.sh
./setup.sh --minimal
```

If `python3` is not Python 3.11 through 3.14, install a supported interpreter and pass it explicitly:

```bash
./setup.sh --minimal --python python3.13
```

## Device check

```bash
./start_server.sh --check-devices
```

Device visibility is determined by OpenVINO. InferBridge cannot use a target that OpenVINO does not expose.

## Model conversion

Install conversion dependencies and convert a catalog model:

```bash
./setup.sh
./setup/linux/convert_model.sh --id tinyllama-1.1b-chat-fp16
```

Gated Hugging Face models require accepted model terms and an `HF_TOKEN` configured in `.env` or the shell.

## Start InferBridge

```bash
./start_server.sh --model tinyllama-1.1b-chat-fp16 --device CPU
./start_server.sh --check-devices
./start_server.sh --mock
```

Open the built-in InferBridge UI at `http://localhost:8000`.

## Driver caveats

- CPU should work once the Python and OpenVINO packages install.
- GPU requires Intel's Linux GPU runtime and driver stack plus render-device permissions.
- NPU requires Intel's Linux NPU driver, supported hardware, and a compatible kernel.
- Do not assume NPU availability merely because `lspci` shows an AI or NPU-like device.
- Start GPU and NPU validation with `./start_server.sh --check-devices`.

## Troubleshooting

- Permission denied on scripts: run `chmod +x setup.sh start_server.sh setup/*.sh setup/linux/*.sh`.
- Missing virtual environment: run `./setup.sh --minimal`.
- Missing `lspci`: run `sudo dnf install -y pciutils`.
- OpenVINO sees only CPU: install or verify Intel GPU or NPU drivers, then rerun device discovery.
- Import errors: remove and recreate `.venv`, then rerun setup.
- Gated Hugging Face models: set `HF_TOKEN=hf_...` only after accepting the model license.
- Corporate TLS or proxy failures: configure `REQUESTS_CA_BUNDLE`, `SSL_CERT_FILE`, and/or `HTTPS_PROXY` before installing dependencies or converting models.
