# InferBridge on Linux

InferBridge for Linux is a local OpenVINO AI server with an OpenAI-compatible API on
`127.0.0.1` and a browser UI for downloading, converting and chatting with models. It
runs as your normal user, needs no system Python, and keeps models and settings in
`~/.local/share/InferBridge`.

- **Supported:** x86_64 with glibc 2.35 or newer. Tested in CI on Ubuntu 22.04 and 24.04,
  Debian 12 and Fedora 44 (install, upgrade, run, uninstall as a fresh user; real CPU
  inference with a tiny test model).
- **CPU** inference works out of the box. **Intel GPU** (Arc, Iris Xe, Core Ultra graphics)
  and **Intel NPU** (Core Ultra) need Intel's Linux drivers, below. GPU and NPU support on
  Linux has not yet been verified on physical hardware by the project; please report results.
- Not supported: ARM64, musl/Alpine, NVIDIA/AMD GPUs (CPU still works on those machines).

## Install

Download from the [GitHub release](https://github.com/Quazmoz/InferBridge/releases) for
your version, together with `InferBridge-<version>-linux-SHA256SUMS.txt`, and verify:

```bash
sha256sum --ignore-missing -c InferBridge-*-linux-SHA256SUMS.txt
```

Pre-release builds from `dev` are on the
[Linux distribution workflow](https://github.com/Quazmoz/InferBridge/actions/workflows/linux-distribution.yml)
runs as the **InferBridge-linux-x86_64-development** artifact (GitHub sign-in required,
kept for seven days). Never install a Windows download on Linux.

### Ubuntu 22.04 / 24.04 and Debian 12 (.deb)

```bash
sudo apt install ./InferBridge-<version>-linux-x86_64.deb
inferbridge
```

`apt` installs the few host libraries it needs (libstdc++, libgomp, libtbb, libnuma).

### Fedora (.rpm, tested on Fedora 44)

```bash
sudo dnf install ./InferBridge-<version>-linux-x86_64.rpm
inferbridge
```

### Any distribution, no root (portable tarball)

```bash
tar -xzf InferBridge-<version>-linux-x86_64-portable.tar.gz -C ~/Applications
~/Applications/InferBridge-<version>-linux-x86_64/InferBridge install
inferbridge
```

`install` adds `~/.local/bin/inferbridge`, the application menu entries and icon for your
user only. The tarball also runs in place without `install`: `./InferBridge`.
Host libraries if they are missing: Ubuntu/Debian
`sudo apt install libstdc++6 libgomp1 libtbb12 libnuma1 libatomic1`, Fedora
`sudo dnf install libstdc++ libgomp tbb numactl-libs libatomic libxcrypt-compat`.

The packages put the read-only application in `/opt/inferbridge`. Only installing the
package or drivers needs `sudo`; InferBridge itself never runs as root and never writes to
its install directory.

## First run

Start **InferBridge** from the application menu or run `inferbridge`. The browser opens
the local UI (normally `http://127.0.0.1:8000/`; another free port is chosen if 8000 is
busy). The setup screen lists what OpenVINO actually detected. Pick **CPU** first, choose
a catalog model after reviewing its size and license, and wait for the download and
conversion to finish. Then chat, or point an OpenAI client at the endpoint shown under
**Network / API** (normally `http://127.0.0.1:8000/v1`).

Closing the browser tab leaves InferBridge running. Stop it with **Quit InferBridge** in the
menu, the tray icon where your desktop shows one, or `inferbridge stop`.

### Why a browser UI on Linux

The Windows edition has a native WinUI app; WinUI is Windows-only. On Linux the same full
browser UI (it is the reference UI that the native app mirrors) is served locally and opened
in your default browser. It works the same on GNOME, KDE, X11, Wayland and over SSH
port forwarding, and adds no second browser engine to the download. Prefer a separate
window without tabs? `inferbridge open --app-window` uses Chromium, Chrome, Edge or Brave in
app mode when one is installed.

The tray icon is optional. It appears on KDE Plasma and other desktops with a system tray.
Stock GNOME shows tray icons only with the AppIndicator extension; without it, use the
menu entries or the commands below. Every function is in the browser UI.

## Everyday commands

| Command | What it does |
|---|---|
| `inferbridge` | Launch (or bring up) the app and open the browser UI |
| `inferbridge start` / `stop` | Start in the background without a browser / stop it |
| `inferbridge status [--json]` | URL, readiness, data and service state |
| `inferbridge open [--app-window]` | Open the UI, starting InferBridge if needed |
| `inferbridge logs [-f] [-n 100]` | Show the application logs |
| `inferbridge doctor [--all-distros]` | Check drivers, permissions and setup, with fix commands |
| `inferbridge service enable` / `disable` | Run as a systemd user service at login |
| `inferbridge autostart enable` / `disable` | Start the tray app at login instead |
| `inferbridge update-check` | Look for a newer release (contacts GitHub only when you run it) |
| `inferbridge uninstall [--purge --yes]` | Remove your integration (and optionally data) |

Only one InferBridge runs per data directory. Launching again reconnects to the running
instance. A busy default port is skipped automatically; an explicit `--port` or
`OV_LLM_PORT` that is busy fails clearly instead of disturbing the other program.

## Run in the background (systemd user service)

```bash
inferbridge stop                 # the service needs the data directory to itself
inferbridge service enable       # enables and starts inferbridge.service for your user
inferbridge status
```

The service runs as you, starts at login, restarts after a crash (at most three times in
two minutes) and uses the same models, settings and UI. To start it at boot before you log
in, an administrator runs `sudo loginctl enable-linger "$USER"`. Logs:
`journalctl --user -u inferbridge.service` and `inferbridge logs`. Disable with
`inferbridge service disable`. Non-secret overrides go in
`systemctl --user edit inferbridge.service` (for example `Environment=OV_LLM_PORT=8000`).
Never put tokens in the unit. A unit you edited is never overwritten or removed by
InferBridge.

## Intel GPU and NPU setup

Run `inferbridge doctor` first: it checks the kernel driver, device permissions, your
groups, the user-space drivers and what OpenVINO sees, and prints the exact commands for
your distribution. InferBridge only offers a device that OpenVINO lists; an explicit GPU or
NPU request never silently falls back to CPU.

### GPU (Arc, Iris Xe, Core Ultra graphics)

Ubuntu 24.04 / Debian 12:

```bash
sudo apt install intel-opencl-icd libze-intel-gpu1 libze1 ocl-icd-libopencl1
sudo usermod -aG render "$USER"     # then log out and back in
```

Newer GPUs (Arc B-series, Lunar Lake, Panther Lake) need a newer runtime and kernel than
Ubuntu 24.04 ships: use the hardware-enablement kernel
(`sudo apt install --install-recommends linux-generic-hwe-24.04`) and Intel's
`ppa:kobuk-team/intel-graphics`. Ubuntu 22.04: install the compute runtime from
[Intel's repository](https://dgpu-docs.intel.com/driver/client/overview.html).

Fedora:

```bash
sudo dnf install intel-compute-runtime intel-opencl intel-level-zero oneapi-level-zero ocl-icd
sudo usermod -aG render "$USER"     # then log out and back in
```

Check with `clinfo -l` (an Intel platform should be listed), then `inferbridge doctor`.

### NPU (Intel Core Ultra)

The `intel_vpu` kernel driver is in Linux 6.3+; Intel recommends 6.8 or newer and newer
still for Lunar Lake and Panther Lake. The user-space driver is separate:

- **Fedora 43+:** `sudo dnf install intel-npu-driver oneapi-level-zero`
- **Ubuntu 24.04:** the `ubuntu2404` packages from
  [intel/linux-npu-driver releases](https://github.com/intel/linux-npu-driver/releases)
  (`sudo apt install ./intel-*.deb` plus `libze1`), or `sudo snap install intel-npu-driver`.
  Intel no longer ships NPU packages for Ubuntu 22.04.

Give your user access to `/dev/accel/accel0` (once):

```bash
echo 'SUBSYSTEM=="accel", KERNEL=="accel*", GROUP="render", MODE="0660"' | sudo tee /etc/udev/rules.d/10-intel-vpu.rules
sudo udevadm control --reload-rules && sudo udevadm trigger --subsystem-match=accel
sudo usermod -aG render "$USER"     # then log out and back in
```

The NPU runs one model at a time and only through the exact `NPU` device; combined device
modes (`AUTO:…`, `HETERO:…`) and the resident chat/embedding/reranker slots behave exactly
as on Windows, see [Device support](DEVICE_SUPPORT.md).

## Hugging Face, API keys and the keyring

Downloads and conversions run locally with the bundled converter; no system Python or pip
is used. For gated models, accept the model's terms on Hugging Face and add your token in
**Hugging Face access** in the UI. API keys you create under **Network / API** and the
Hugging Face token are stored in your desktop keyring (GNOME Keyring, KWallet or
KeePassXC through the Secret Service API, via `secret-tool`) when it is available, so they
survive restarts. Install `secret-tool` with `sudo apt install libsecret-tools` (Ubuntu,
Debian) or `sudo dnf install libsecret` (Fedora). Without a keyring they are kept for the
current session only and never written to disk; for a key that must survive restarts on a
headless machine, set `OV_LLM_API_KEY` (and `HF_TOKEN`) in the service environment with
`systemctl --user edit inferbridge.service`. Keep tokens out of command lines.

## Network access and firewall

InferBridge listens on `127.0.0.1` only. To use it from another machine, set an API key
first, then enable LAN access under **Network / API** (it refuses without a key). Then
allow the port on trusted networks only:

```bash
sudo ufw allow from 192.168.1.0/24 to any port 8000 proto tcp                   # Ubuntu
sudo firewall-cmd --zone=home --add-port=8000/tcp --permanent && sudo firewall-cmd --reload   # Fedora
```

See [LAN access](LAN_ACCESS.md).

## Files and data

| What | Where |
|---|---|
| Models, settings, caches, logs | `~/.local/share/InferBridge` (`$XDG_DATA_HOME/InferBridge`), private to you (0700) |
| Logs | `~/.local/share/InferBridge/logs/` (`inferbridge logs`) |
| Application (packages) | `/opt/inferbridge`, `/usr/bin/inferbridge`, `/usr/share/applications/inferbridge.desktop`, `/usr/lib/systemd/user/inferbridge.service` |
| Your integration (tarball) | `~/.local/bin/inferbridge`, `~/.local/share/applications/inferbridge*.desktop`, `~/.config/systemd/user/inferbridge.service`, `~/.config/autostart/inferbridge.desktop` |

`--data-dir PATH` or `OV_LLM_DATA_DIR` chooses another data directory; `--portable` keeps
data in `data/` beside a tarball. Data written by an older version is upgraded in place
with a backup of the small configuration files; a newer data schema than the app
understands is refused rather than damaged.

## Update

Check with `inferbridge update-check`, or enable update checks under **About** in the UI.
Quit InferBridge (and `inferbridge service disable` if you use the service), then:

- **.deb / .rpm:** `sudo apt install ./InferBridge-<new>-linux-x86_64.deb` or
  `sudo dnf install ./InferBridge-<new>-linux-x86_64.rpm`
- **Tarball:** extract the new version next to the old one and run its `InferBridge install`.
  Your menu entries, command, service unit and autostart entry move to the new version; a
  file you edited is kept and reported. Then delete the old directory.

Models and settings are reused. Re-enable the service if you disabled it.

## Uninstall

```bash
inferbridge uninstall                 # stops it, removes the service, autostart and menu entries
sudo apt remove inferbridge           # or: sudo dnf remove inferbridge
```

For a tarball, `inferbridge uninstall --remove-app` also deletes the extracted directory.
Your models and settings are kept unless you add `--purge --yes`, which deletes
`~/.local/share/InferBridge` (run it before removing the package). Removing the package
never touches any user's data.

## Troubleshooting

| Symptom | What to do |
|---|---|
| `inferbridge: command not found` after a tarball install | Log out and back in, or add `~/.local/bin` to `PATH` |
| The browser does not open | Open the URL from `inferbridge status`; install `xdg-utils` |
| No tray icon on GNOME | Expected without the AppIndicator extension; use the menu or `inferbridge stop` |
| GPU or NPU missing from the device list | `inferbridge doctor`, then follow its fix commands; log out and back in after group changes |
| "Another application instance owns this data directory" | `inferbridge stop`, then start the service or app again |
| Port 8000 in use | InferBridge picks another free port; `inferbridge status` shows it |
| Startup fails | `inferbridge logs`; `inferbridge start` prints why; `journalctl --user -u inferbridge.service` for the service |
| `error while loading shared libraries` | Install the host libraries listed under Install |
| Keys forgotten after restart | Install `secret-tool` (see the keyring section) or set `OV_LLM_API_KEY` |

When reporting a problem, attach `inferbridge doctor --json` and the sanitized diagnostics
export from the UI after reviewing them for private data. For GPU/NPU reports include the
CPU/GPU/NPU model, kernel, driver versions, model, precision and requested device.

## For developers

### Build and reproduce

On native Linux x86_64 with Python 3.12 and `uv==0.11.21`, from a clean commit:

```bash
python3.12 scripts/build_linux_distribution.py
python3.12 scripts/validate_linux_bundle.py --archive dist/linux/*-portable.tar.gz --evidence build/linux/evidence
sudo apt install rpm   # rpmbuild, for the .rpm
python3.12 scripts/build_linux_packages.py --archive dist/linux/*-portable.tar.gz --output dist/linux --finalize
python3.12 scripts/verify_linux_release.py --directory dist/linux --version "$(python3.12 -c 'from app.version import __version__; print(__version__)')"
```

The archive uses a private relocatable CPython 3.12.12
([python-build-standalone](https://github.com/astral-sh/python-build-standalone), verified
by uv) and hash-locked wheels from `requirements/linux.lock` (CPU-only PyTorch for
conversion). File order, owners, modes and timestamps are normalized to the commit time
(`SOURCE_DATE_EPOCH`) in the tarball, the .deb and the .rpm, and the same input archive
produces byte-identical .deb files (tested). Byte-identical archives across different
build machines are not yet certified, because uv and the wheel installer are only
version-pinned. Each release carries an SPDX 2.3 SBOM (bundled CPython and every wheel,
with purls), `THIRD-PARTY-NOTICES.txt`, the dependency freeze and `build-info.json` with
the source commit; tag builds also get a GitHub build-provenance attestation
(`gh attestation verify <file> -R Quazmoz/InferBridge`).

The [release workflow](../.github/workflows/github-release.yml) builds and attaches the
Linux assets beside the Windows ones when a version tag is pushed. A manual run with
`dry_run` builds and verifies them from a branch without publishing anything.

### Source checkout

For development from a source checkout (Python 3.11+ virtual environment) see
[Ubuntu](UBUNTU.md) and [Fedora](FEDORA.md): `./setup.sh --minimal`, then
`./start_server.sh --mock` or `./.venv/bin/python -m app.desktop_launcher --mock`.

### Qualification

Every `dev` push runs [Linux source smoke](https://github.com/Quazmoz/InferBridge/actions/workflows/linux-smoke.yml)
and the [Linux distribution workflow](https://github.com/Quazmoz/InferBridge/actions/workflows/linux-distribution.yml):

| Check | Where |
|---|---|
| Ruff, unit tests, shell syntax | Ubuntu 22.04 |
| Archive: bootstrap, native imports, converter, menu entries, user unit, duplicate launch, occupied port, restart, SIGINT/SIGTERM, crash and orphan recovery, no writes to the install directory, mock OpenAI API | Ubuntu 22.04, Ubuntu 24.04, Fedora 44 (unprivileged user) |
| .deb: install over an older-numbered package, fresh user, `inferbridge` start/status/stop, API-key enforcement, OpenAI chat/stream/embeddings/rerank/slots/lifecycle contract (mock), systemd `--user` enable/restart/stop/disable, uninstall and purge leave nothing | Ubuntu 22.04 and 24.04 VMs |
| Same package checks without systemd, from minimal images (proves the declared dependencies) | ubuntu:22.04, ubuntu:24.04, debian:12 (.deb); fedora:44 (.rpm) |
| Tarball: fresh-user `install`, upgrade from the previous `dev` build with data preserved, systemd user service, uninstall | Ubuntu 24.04 VM |
| Real CPU inference: convert a 16 MiB random-weight Llama, generate and stream with the engine, then import, load, chat and stream through the packaged app's OpenAI API | Ubuntu 22.04 (GitHub-hosted, AMD EPYC) |

Not covered by CI: physical Intel GPU or NPU execution, graphical GNOME/KDE sessions
(Wayland and X11), and the tray icon. Random weights prove runtime execution, not answer
quality. Older evidence for commit `fea8bf3` is retained in
[certification/linux](certification/linux/2026-10-30-fea8bf3/README.md).
