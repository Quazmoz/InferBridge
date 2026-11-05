"""Linux accelerator and environment doctor: honest findings with exact fix commands.

Everything here reads files (``/sys``, ``/dev``, ``/etc``, library directories) and never
needs root, network access or a running server. A present PCI device, kernel driver or
library is only a prerequisite: OpenVINO's own device list remains the authority on
whether CPU, GPU or NPU can actually be selected. Unit tests pass a fake ``root``.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import stat
from dataclasses import asdict, dataclass, field
from pathlib import Path

INTEL_VENDOR = "0x8086"
# Intel NPU PCI device IDs (Meteor Lake, Arrow Lake, Lunar Lake, Panther Lake). Any Intel
# "processing accelerator" (class 0x1200) bound to intel_vpu is reported as well.
NPU_DEVICE_IDS = {"0x7d1d": "Meteor Lake", "0xad1d": "Arrow Lake", "0x643e": "Lunar Lake"}
NPU_DEVICE_IDS["0xb03e"] = "Panther Lake"
LIBRARY_DIRS = (
    "usr/lib/x86_64-linux-gnu",
    "lib/x86_64-linux-gnu",
    "usr/lib64",
    "lib64",
    "usr/lib",
    "usr/local/lib",
    "snap/intel-npu-driver/current/usr/lib/x86_64-linux-gnu",
)
SECRET_TOOL_FIX = {
    "debian": ["sudo apt install libsecret-tools"],
    "fedora": ["sudo dnf install libsecret"],
}
RENDER_GROUP_FIX = ['sudo usermod -aG render "$USER"', "# then log out and back in"]
GPU_RUNTIME_FIX = {
    "debian": [
        "sudo apt install intel-opencl-icd libze-intel-gpu1 libze1 ocl-icd-libopencl1",
        "# Arc B-series, Lunar Lake or Panther Lake on 24.04: newer runtime from Intel's PPA",
        "sudo add-apt-repository ppa:kobuk-team/intel-graphics && sudo apt update",
        "sudo apt install intel-opencl-icd libze-intel-gpu1 libze1",
    ],
    "fedora": [
        "sudo dnf install intel-compute-runtime intel-opencl intel-level-zero "
        "oneapi-level-zero ocl-icd"
    ],
}
NPU_RUNTIME_FIX = {
    "debian": [
        "# Ubuntu 24.04+: Intel's packages from https://github.com/intel/linux-npu-driver/releases",
        "# (download the ubuntu2404 tarball, extract it, then)",
        "sudo apt install ./intel-*.deb libze1",
        "# or the Canonical snap: sudo snap install intel-npu-driver",
    ],
    "fedora": ["sudo dnf install intel-npu-driver oneapi-level-zero"],
}
NPU_UDEV_FIX = [
    'echo \'SUBSYSTEM=="accel", KERNEL=="accel*", GROUP="render", MODE="0660"\' '
    "| sudo tee /etc/udev/rules.d/10-intel-vpu.rules",
    "sudo udevadm control --reload-rules && sudo udevadm trigger --subsystem-match=accel",
    *RENDER_GROUP_FIX,
]
KERNEL_FIX = {
    "debian": [
        "# Ubuntu 24.04: use the hardware-enablement kernel, then reboot",
        "sudo apt install --install-recommends linux-generic-hwe-24.04",
    ],
    "fedora": ["sudo dnf upgrade --refresh kernel", "# then reboot"],
}


@dataclass
class Finding:
    id: str
    status: str  # ok | info | warning | error
    title: str
    detail: str = ""
    fix: dict[str, list[str]] = field(default_factory=dict)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def os_release(root: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for candidate in (root / "etc/os-release", root / "usr/lib/os-release"):
        for line in _read(candidate).splitlines():
            key, separator, value = line.partition("=")
            if separator:
                values[key.strip()] = value.strip().strip('"')
        if values:
            break
    return values


def distro_family(release: dict[str, str]) -> str:
    ids = {release.get("ID", ""), *release.get("ID_LIKE", "").split()}
    if ids & {"debian", "ubuntu"}:
        return "debian"
    if ids & {"fedora", "rhel", "centos"}:
        return "fedora"
    return "other"


def kernel_version(release: str) -> tuple[int, int]:
    parts = release.split("-", 1)[0].split(".")
    try:
        return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
    except ValueError:
        return 0, 0


@dataclass
class PciDevice:
    address: str
    vendor: str
    device: str
    pci_class: str
    driver: str | None


def pci_devices(root: Path) -> list[PciDevice]:
    devices = []
    base = root / "sys/bus/pci/devices"
    for entry in sorted(base.glob("*")) if base.is_dir() else []:
        driver = entry / "driver"
        devices.append(
            PciDevice(
                address=entry.name,
                vendor=_read(entry / "vendor").lower(),
                device=_read(entry / "device").lower(),
                pci_class=_read(entry / "class").lower(),
                driver=os.path.basename(os.readlink(driver)) if driver.is_symlink() else None,
            )
        )
    return devices


def find_library(root: Path, *names: str) -> str | None:
    for directory in LIBRARY_DIRS:
        for name in names:
            candidate = root / directory / name
            if candidate.exists():
                return "/" + str(candidate.relative_to(root))
    return None


def intel_opencl_icd(root: Path) -> str | None:
    vendors = root / "etc/OpenCL/vendors"
    for icd in sorted(vendors.glob("*.icd")) if vendors.is_dir() else []:
        if "intel" in icd.name.lower() or "igdrcl" in _read(icd).lower():
            return "/" + str(icd.relative_to(root))
    return None


def _group_name(gid: int) -> str:
    import grp  # POSIX only; imported lazily so Windows tooling can import this module.

    try:
        return grp.getgrgid(gid).gr_name
    except KeyError:
        return str(gid)


def configured_groups(user: str, gid: int) -> set[str]:
    """Groups in the account database: what the *next* login session receives."""
    import grp

    names = {_group_name(gid)}
    try:
        names.update(group.gr_name for group in grp.getgrall() if user in group.gr_mem)
    except OSError:
        pass
    return names


def node_access(
    root: Path,
    pattern: str,
    *,
    session_groups: set[str],
    account_groups: set[str],
) -> list[dict]:
    nodes = []
    for path in sorted((root / "dev").glob(pattern)):
        try:
            info = path.stat()
        except OSError:
            continue
        group = _group_name(info.st_gid)
        nodes.append(
            {
                "path": "/" + str(path.relative_to(root)),
                "group": group,
                "mode": stat.filemode(info.st_mode),
                "read_write": os.access(path, os.R_OK | os.W_OK),
                "group_in_session": group in session_groups,
                "group_in_account": group in account_groups,
            }
        )
    return nodes


def _access_finding(kind: str, nodes: list[dict], *, npu: bool) -> Finding:
    usable = [node for node in nodes if node["read_write"]]
    names = ", ".join(f"{n['path']} ({n['mode']} group {n['group']})" for n in nodes)
    if usable:
        return Finding(f"{kind.lower()}-access", "ok", f"{kind} device node is accessible", names)
    stale = [n for n in nodes if n["group_in_account"] and not n["group_in_session"]]
    if stale:
        return Finding(
            f"{kind.lower()}-access",
            "error",
            f"{kind} device group was added, but this session predates it",
            f"{names}. Your account is in '{stale[0]['group']}', this login session is not.",
            {"any": ["# log out and back in (or reboot); `newgrp render` works for one shell"]},
        )
    root_only = npu and all(n["group"] == "root" for n in nodes)
    return Finding(
        f"{kind.lower()}-access",
        "error",
        f"No read/write access to the {kind} device node",
        names,
        {"any": NPU_UDEV_FIX if root_only else RENDER_GROUP_FIX},
    )


def diagnose(
    *,
    root: Path = Path("/"),
    environment: dict[str, str] | None = None,
    openvino_devices: list[str] | None = None,
    data_dir: Path | None = None,
    session_groups: set[str] | None = None,
    account_groups: set[str] | None = None,
    kernel_release: str | None = None,
    machine: str | None = None,
) -> dict:
    env = dict(os.environ if environment is None else environment)
    release = os_release(root)
    family = distro_family(release)
    kernel = kernel_release or platform.release()
    major_minor = kernel_version(kernel)
    machine = machine or platform.machine()
    if session_groups is None:
        session_groups = {_group_name(gid) for gid in os.getgroups()}
    if account_groups is None:
        import pwd

        try:
            account = pwd.getpwuid(os.getuid())
            account_groups = configured_groups(account.pw_name, account.pw_gid)
        except KeyError:
            account_groups = set(session_groups)
    findings: list[Finding] = []
    add = findings.append

    supported_arch = machine == "x86_64"
    add(
        Finding(
            "platform",
            "ok" if supported_arch else "error",
            f"{release.get('PRETTY_NAME', 'Unknown Linux')} on {machine}, kernel {kernel}",
            "" if supported_arch else "InferBridge Linux builds are x86_64 only.",
        )
    )
    if family == "other":
        add(
            Finding(
                "distro",
                "info",
                "Distribution not in the tested set",
                "Tested: Ubuntu 22.04/24.04, Debian 12, Fedora 44. glibc 2.35+ is required.",
            )
        )
    try:
        glibc = os.confstr("CS_GNU_LIBC_VERSION") or ""
    except (ValueError, OSError):
        glibc = ""
    if glibc:
        parts = glibc.split()[-1].split(".")
        glibc_ok = (int(parts[0]), int(parts[1])) >= (2, 35)
        if not glibc_ok:
            add(Finding("glibc", "error", f"{glibc} is older than the required glibc 2.35"))

    # OpenVINO is the authority on what can be selected.
    devices = list(openvino_devices or [])
    add(
        Finding(
            "openvino-devices",
            "ok" if "CPU" in devices else "error",
            "OpenVINO devices: " + (", ".join(devices) if devices else "none"),
            ""
            if devices
            else "OpenVINO could not enumerate devices; run `inferbridge doctor`"
            " from a complete installation and check host libraries (libtbb12, libgomp1).",
        )
    )

    pci = pci_devices(root)
    intel_gpus = [d for d in pci if d.vendor == INTEL_VENDOR and d.pci_class.startswith("0x03")]
    intel_npus = [
        d
        for d in pci
        if d.vendor == INTEL_VENDOR
        and (d.device in NPU_DEVICE_IDS or (d.pci_class.startswith("0x1200") and d.driver))
    ]
    other_gpus = [d for d in pci if d.vendor != INTEL_VENDOR and d.pci_class.startswith("0x03")]
    nodes_gpu = node_access(
        root, "dri/renderD*", session_groups=session_groups, account_groups=account_groups
    )
    nodes_npu = node_access(
        root, "accel/accel*", session_groups=session_groups, account_groups=account_groups
    )
    loader = find_library(root, "libze_loader.so.1", "libze_loader.so")

    if not intel_gpus:
        detail = "OpenVINO's GPU plugin supports Intel graphics only."
        if other_gpus:
            detail += f" Found {len(other_gpus)} non-Intel display adapter(s); use CPU."
        add(Finding("gpu", "info", "No Intel GPU detected", detail))
    else:
        drivers = sorted({d.driver or "none" for d in intel_gpus})
        bound = [d for d in intel_gpus if d.driver in {"i915", "xe"}]
        add(
            Finding(
                "gpu-kernel-driver",
                "ok" if bound else "error",
                f"Intel GPU {', '.join(d.device for d in intel_gpus)}: kernel driver "
                + ", ".join(drivers),
                "" if bound else "No i915/xe driver is bound; the kernel may be too old.",
                {} if bound else KERNEL_FIX,
            )
        )
        if bound:
            add(
                _access_finding("GPU", nodes_gpu, npu=False)
                if nodes_gpu
                else Finding("gpu-access", "error", "No /dev/dri/renderD* node", "", KERNEL_FIX)
            )
        icd = intel_opencl_icd(root)
        gpu_l0 = find_library(root, "libze_intel_gpu.so.1")
        opencl_loader = find_library(root, "libOpenCL.so.1")
        missing = [
            name
            for name, present in (
                ("Intel OpenCL ICD (intel-opencl-icd / intel-opencl)", icd),
                ("OpenCL ICD loader (libOpenCL.so.1)", opencl_loader),
                ("Level Zero GPU driver (libze_intel_gpu.so.1)", gpu_l0),
            )
            if not present
        ]
        add(
            Finding(
                "gpu-runtime",
                "error" if missing else "ok",
                "Intel GPU compute runtime "
                + ("missing: " + "; ".join(missing) if missing else "installed"),
                ", ".join(path for path in (icd, opencl_loader, gpu_l0) if path),
                GPU_RUNTIME_FIX if missing else {},
            )
        )
        if bound and not missing and "GPU" not in " ".join(devices):
            add(
                Finding(
                    "gpu-openvino",
                    "warning",
                    "Drivers look installed, but OpenVINO did not list a GPU",
                    "Check `clinfo -l` and that the compute runtime supports this GPU "
                    "(newer GPUs need a newer runtime and kernel).",
                    GPU_RUNTIME_FIX,
                )
            )
        if major_minor < (6, 8):
            add(
                Finding(
                    "gpu-kernel-version",
                    "warning",
                    f"Kernel {kernel} predates 6.8",
                    "Intel lists 6.8+ for Meteor Lake and newer graphics (24.04 HWE).",
                    KERNEL_FIX,
                )
            )

    if not intel_npus and not nodes_npu:
        add(
            Finding(
                "npu",
                "info",
                "No Intel NPU detected",
                "NPUs ship with Intel Core Ultra processors (Meteor Lake and newer).",
            )
        )
    else:
        names = ", ".join(NPU_DEVICE_IDS.get(d.device, d.device) for d in intel_npus) or "NPU"
        bound = any(d.driver == "intel_vpu" for d in intel_npus) or bool(nodes_npu)
        add(
            Finding(
                "npu-kernel-driver",
                "ok" if bound else "error",
                f"Intel NPU ({names}): kernel driver {'intel_vpu' if bound else 'not loaded'}",
                "" if bound else "intel_vpu is in Linux 6.3+; Intel recommends 6.8 or newer.",
                {} if bound else KERNEL_FIX,
            )
        )
        if bound:
            add(
                _access_finding("NPU", nodes_npu, npu=True)
                if nodes_npu
                else Finding("npu-access", "error", "No /dev/accel/accel* node", "", KERNEL_FIX)
            )
        umd = find_library(root, "libze_intel_npu.so.1", "libze_intel_npu.so")
        legacy = find_library(root, "libze_intel_vpu.so.1", "libze_intel_vpu.so")
        missing = [
            name
            for name, present in (
                ("NPU user-mode driver (libze_intel_npu.so)", umd or legacy),
                ("Level Zero loader (libze_loader.so.1)", loader),
            )
            if not present
        ]
        add(
            Finding(
                "npu-runtime",
                "error" if missing else "ok",
                "Intel NPU user-space driver "
                + ("missing: " + "; ".join(missing) if missing else "installed"),
                "Legacy libze_intel_vpu: update to NPU driver 1.16 or newer."
                if legacy and not umd
                else "",
                NPU_RUNTIME_FIX if missing or (legacy and not umd) else {},
            )
        )
        if bound and not missing and "NPU" not in " ".join(devices):
            add(
                Finding(
                    "npu-openvino",
                    "warning",
                    "NPU driver looks installed, but OpenVINO did not list an NPU",
                    "Firmware or driver/OpenVINO version mismatch; update intel-npu-driver.",
                    NPU_RUNTIME_FIX,
                )
            )

    session = env.get("XDG_SESSION_TYPE") or ("wayland" if env.get("WAYLAND_DISPLAY") else "")
    session = session or ("x11" if env.get("DISPLAY") else "none")
    desktop = env.get("XDG_CURRENT_DESKTOP", "")
    tray_note = (
        "GNOME shows tray icons only with the AppIndicator extension; use the menu entries "
        "or `inferbridge stop` instead."
        if "GNOME" in desktop.upper()
        else "The tray icon is optional; the browser UI and `inferbridge` CLI cover everything."
    )
    add(Finding("session", "info", f"Desktop session: {session} {desktop}".strip(), tray_note))
    has_bus = bool(env.get("DBUS_SESSION_BUS_ADDRESS")) or (
        bool(env.get("XDG_RUNTIME_DIR")) and Path(env.get("XDG_RUNTIME_DIR", ""), "bus").exists()
    )
    keyring = bool(shutil.which("secret-tool", path=env.get("PATH"))) and has_bus
    add(
        Finding(
            "keyring",
            "ok" if keyring else "info",
            "Desktop keyring (Secret Service) "
            + ("available" if keyring else "unavailable: keys and tokens last one session"),
            "" if keyring else "Or set OV_LLM_API_KEY / HF_TOKEN in the launch environment.",
            {} if keyring else SECRET_TOOL_FIX,
        )
    )
    if data_dir is not None:
        try:
            mode = data_dir.stat().st_mode
            usage = shutil.disk_usage(data_dir)
        except OSError:
            mode, usage = 0, None
        if mode & 0o077:
            add(
                Finding(
                    "data-permissions",
                    "warning",
                    f"Data directory is readable by other users ({stat.filemode(mode)})",
                    str(data_dir),
                    {"any": [f'chmod 700 "{data_dir}"']},
                )
            )
        if usage is not None and usage.free < 10 * 1024**3:
            add(
                Finding(
                    "disk-space",
                    "warning",
                    f"Only {usage.free // 1024**3} GiB free for models",
                    "Typical 1–8B models need 2–10 GiB each, plus conversion space.",
                )
            )
    return {
        "distribution": {
            "id": release.get("ID", ""),
            "version": release.get("VERSION_ID", ""),
            "family": family,
            "kernel": kernel,
            "machine": machine,
        },
        "openvino_devices": devices,
        "findings": [asdict(finding) for finding in findings],
        "errors": sum(finding.status == "error" for finding in findings),
        "note": "Device nodes and drivers are prerequisites only; OpenVINO's device list "
        "decides what InferBridge can select. Explicit NPU/GPU requests never fall back.",
    }


_SYMBOLS = {"ok": "✓", "info": "·", "warning": "!", "error": "✗"}


def render(report: dict, *, all_distros: bool = False) -> str:
    family = report["distribution"]["family"]
    lines = []
    for item in report["findings"]:
        lines.append(f"{_SYMBOLS.get(item['status'], '?')} {item['title']}")
        if item["detail"]:
            lines.append(f"    {item['detail']}")
        fixes = item["fix"]
        for name in fixes if all_distros or family == "other" else (family, "any"):
            if name in fixes:
                label = {"debian": "Ubuntu/Debian", "fedora": "Fedora", "any": "Fix"}[name]
                lines.append(f"    {label}:")
                lines.extend(f"      {command}" for command in fixes[name])
    lines.append("")
    lines.append(report["note"])
    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover - manual diagnostics
    print(json.dumps(diagnose(), indent=2))
