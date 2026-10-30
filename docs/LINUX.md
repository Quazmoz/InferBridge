# Linux desktop development distribution

Linux support is experimental and currently supports Ubuntu and Fedora. InferBridge is still Windows-first, so start with CPU validation before spending time on GPU/NPU driver work.

## Download and launch

The P0 format is a **Linux x86_64 portable archive with a private CPython runtime**.
It includes the existing FastAPI backend, browser UI, OpenVINO GenAI and model
conversion dependencies. It needs no source checkout, system Python, pip, virtual
environment, root access, or Docker on the user's machine. Models and Intel drivers
are separate. ARM64, Alpine/musl and macOS cannot run this Linux download.

Development artifacts live on successful
[Linux development distribution runs](https://github.com/Quazmoz/InferBridge/actions/workflows/linux-distribution.yml)
for `dev`, under **InferBridge-linux-x86_64-development**. GitHub requires sign-in
to download Actions artifacts; downloads expire after seven days. There is no Linux
GitHub Release or package repository yet. Do not substitute Windows release assets.

After unzipping the Actions artifact, in that download directory:

```bash
sha256sum -c InferBridge-1.1.1-dev.1-linux-SHA256SUMS.txt
tar -xzf InferBridge-1.1.1-dev.1-linux-x86_64-portable.tar.gz
cd InferBridge-1.1.1-dev.1-linux-x86_64
./InferBridge --install-desktop
./InferBridge
```

Use the version in your actual artifact filename. Keep the entire extracted
directory together. Paths containing spaces and Unicode are supported. The menu
entries **InferBridge** and **Quit InferBridge** work in GNOME and KDE menus; the
graphical desktop itself still needs manual qualification on Wayland and X11.
The tray is optional. Closing the browser tab leaves the application running;
choose **Quit InferBridge** or run `./InferBridge --quit` to stop the owned backend.
Launching again reconnects to the same controller and server.

Ubuntu 22.04/24.04 host libraries (usually present on a desktop):

```bash
sudo apt install libstdc++6 libgcc-s1 libgomp1 libnuma1 libtbb12 libatomic1 ocl-icd-libopencl1
```

Fedora 44 host libraries:

```bash
sudo dnf install libstdc++ libgcc libgomp numactl-libs tbb libatomic ocl-icd libxcrypt-compat
```

Only installing missing OS libraries/drivers needs administrator access. Portable
application extraction, launch and desktop registration run as a normal user.
These glibc builds target Ubuntu 22.04 or newer, with Fedora 44 tested separately
by CI. Driver-dependent GPU/NPU execution is not certified by package startup.

## First model, chat and API

Start without `--mock` for real inference. In first-run setup, inspect the actual
OpenVINO device inventory and select **CPU** first. Choose a compatible catalog
model only after reviewing download size, license and memory requirements. Use
Model Library to download/convert a model, or import a complete local OpenVINO
model directory. Wait for preparation and loading to finish, then send a chat
message. Explicit NPU requests remain NPU requests and fail visibly if unavailable.
Composite expressions remain experimental and model/runtime dependent.

Model weights are never included in the base download. An offline installation can
start and use previously imported compatible models; an uncached model download
requires networking. Gated models require accepted terms and an `HF_TOKEN` in the
launch environment. Do not put tokens in command-line arguments or support reports.

The browser exposes models, context settings, diagnostics, chat and Network / API
settings. Copy the active OpenAI endpoint there for VS Code clients or Open WebUI:
normally `http://127.0.0.1:8000/v1`, with the actual selected port shown in the UI.
Configure an API key before enabling LAN access; desktop startup remains loopback
only unless explicitly configured otherwise. See [LAN access](LAN_ACCESS.md).
Linux currently retains GUI-generated keys in memory only; Windows DPAPI remains
Windows-only. Set `OV_LLM_API_KEY` in the launch environment for a key that must
remain active across backend restarts. Keep it out of `.desktop` commands and shell
history. Missing credentials keep LAN binding blocked after a restart.

For an installation-only check without weights or inference:

```bash
./InferBridge --mock
# Chat now uses deterministic mock output, not OpenVINO inference.
./InferBridge --quit
```

## Data, upgrades and uninstall

By default all models, config, cache, logs and diagnostics live under
`$XDG_DATA_HOME/InferBridge` (absolute XDG path) or `~/.local/share/InferBridge`.
Logs are `logs/tray.log` and `logs/desktop.log` under that directory. `--data-dir`
and `OV_LLM_DATA_DIR` explicitly override it; `LOCALAPPDATA` remains a compatibility
override. `--portable` opts into a sibling `data/` beside the extracted launcher.
Without that flag, a writable install directory is not required.

To upgrade, quit the old application, extract the new archive into a new directory,
run the new `--install-desktop` command, then launch it. Default XDG user data is
reused without replacing models or settings. If you used `--portable`, keep the old
data directory and use the new launcher's `--data-dir /absolute/path/to/old/data`;
do not extract a new application over your only copy of user data.

To uninstall, run `./InferBridge --quit`, then `./InferBridge --uninstall-desktop`,
and delete the extracted application directory. User data is preserved. With
`--portable`, move its `data/` elsewhere before deleting the directory. Removing
the old version's menu entries cannot remove entries registered by a newer version.
With sibling data, quit using `./InferBridge --portable --quit`; with an explicit
data root, include the same `--data-dir` when quitting. Desktop menu entries use
default XDG mode. Autostart is not enabled. `.deb`, `.rpm`, AppImage and Flatpak are deferred until
their separate installer/upgrade/uninstall paths can be qualified.

## Build and qualification

On a native Linux x86_64 development host, install `uv==0.11.21` and Python 3.12
as build tools, commit qualified work, then run:

```bash
python3.12 scripts/build_linux_distribution.py
python3.12 scripts/validate_linux_bundle.py \
  --archive dist/linux/InferBridge-1.1.1-dev.1-linux-x86_64-portable.tar.gz \
  --evidence build/linux/evidence
```

The builder uses a private relocatable CPython 3.12.12 from
[python-build-standalone](https://github.com/astral-sh/python-build-standalone),
verified by uv, and hash-locked Linux wheels from `requirements/linux.lock`.
CPU-only PyTorch is used for conversion; OpenVINO GenAI remains the inference
runtime. The Windows release requirements and Windows packaging remain separate.
Archive order, ownership, permissions and timestamps are normalized; byte-for-byte
reproducibility across different builders is not yet certified. Native libraries,
Python/wheel licenses, dependency inventory and build provenance accompany the app.
System glibc, graphics/NPU drivers and optional shell helpers are not bundled.

| Environment | Source smoke | Packaged mock | Real CPU | Real GPU | Real NPU |
|---|---|---|---|---|---|
| Ubuntu 22.04 x86_64 / source Python 3.11, 3.12 | Passed | Passed | Random-model probe passed on AMD EPYC; Intel unverified | Not tested | Not tested |
| Ubuntu 24.04 x86_64 / source Python 3.11, 3.12 | Passed | Passed | Not tested | Not tested | Not tested |
| Fedora 44 x86_64 container | Not tested | Passed with the same archive | Not tested | Not tested | Not tested |
| GNOME/KDE, Wayland/X11 | Not tested | Headless CI only | Not tested | Not tested | Not tested |
| macOS ARM64 development host | Unit/static and Chromium browser checks | Unsupported Linux artifact host | Not tested | Unsupported | Unsupported |

Results above qualify code commit `fea8bf35529d34336ce9797954d664524fe866cd`
on 30 October 2026: [Linux source matrix](https://github.com/Quazmoz/InferBridge/actions/runs/41072708478),
[portable and real CPU jobs](https://github.com/Quazmoz/InferBridge/actions/runs/41072708370),
and [Windows full source qualification](https://github.com/Quazmoz/InferBridge/actions/runs/41072708378).
Local checks passed 1,697 unit tests (17 platform skips), all 44 Chromium tests,
pinned Ruff lint/format, shell syntax and all 37 embedded JavaScript blocks.
Each packaged mock API run passed 13 checks with one warning for optional
tool/structured outputs, and no failures. The archive passed bootstrap, native
library/converter import, CLI discovery, desktop registration/removal, duplicate
launch, occupied-port refusal, restart, SIGINT, SIGTERM, owner crash recovery and
no-installation-write checks from paths with spaces and Unicode.

[Retained reports and SHA-256 hashes](certification/linux/2026-10-30-fea8bf3/README.md)
record the CPU probe on **AMD EPYC 9V45**, Ubuntu **22.04.5**, kernel
**6.8.0-1070-azure**, OpenVINO **2026.2.1**, GenAI/tokenizers **2026.2.1.0**,
Optimum Intel **2.0.0** and CPU PyTorch **2.12.0+cpu**. The 6,476,069-byte
FP16 test-model input converted and generated/streamed through the bundled
application engine in 13.16 seconds; maximum single-process high-water RSS
was 603,352 KiB. This is native x86_64 CI execution on AMD, not emulation,
Intel CPU certification, useful-chat quality, or a full real-inference API run.
No suitable physical Intel Linux CPU/GPU/NPU or graphical desktop was available.

Consult the exact commit and run conclusion before treating a CI gate as passed.
The separate `cpu-probe` CI job downloads at most 16 MiB of files from the pinned
`trl-internal-testing/tiny-random-LlamaForCausalLM` revision
`2c542e4731d42d3bb68324c4eca16df9f93b8b33`, excludes pickle weights, converts FP16
OpenVINO IR, and tests direct CPU generation, streaming and cleanup using the
bundle's own interpreter and application engine. It uploads exact CPU, kernel,
runtime, model, commit, elapsed-time and peak-process-RSS evidence. Random weights
test runtime execution and do not establish useful chat quality or full CPU API
certification. These models remain outside the downloadable application.

Mock output, native library imports and CPU enumeration do not certify real inference.
Hardware reports must include the application commit, CPU/GPU/NPU model, OS/kernel,
driver, OpenVINO/GenAI versions, requested and actual device, model revision,
precision, peak memory, and real streaming/chat result. Review diagnostics for
private data before attaching them to an issue.

## Troubleshooting downloads

Start `./InferBridge --headless --no-browser` in a terminal to review errors.
Run `./InferBridge --check-devices` for actual OpenVINO inventory and GPU/NPU
device-node access hints. A node's presence does not certify an accelerator.
Use `./InferBridge --bootstrap-smoke` for import startup and `--native-smoke` for
OpenVINO/tokenizer and converter import checks without weights. On an occupied
default port, the controller selects a free port; an explicit `--port` or
`OV_LLM_PORT` fails clearly rather than stopping another application. If a previous
launcher crashed, allow its server owner monitor to finish cleanup, then relaunch.
Relative XDG paths are ignored; unwritable data directories fail with an error.

Optional `zenity`/`kdialog`, `xdg-utils`, `wl-clipboard` or `xclip` improve dialogs,
folder opening and clipboard behavior. No tray extension is required. For GPU/NPU
failures, check System Doctor and the device-node diagnostics below before changing
the target; PCI detection alone does not establish OpenVINO support.

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
