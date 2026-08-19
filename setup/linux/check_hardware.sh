#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/../.." >/dev/null 2>&1 && pwd)"
VENV_PYTHON="$REPO_ROOT/.venv/bin/python"

# shellcheck disable=SC1091
. "$SCRIPT_DIR/lib.sh"
OS_ID="$(detect_linux_id)"
PLATFORM_NAME="$(linux_platform_name "$OS_ID")"

has_rw_device() {
    local pattern="$1"
    local path
    shopt -s nullglob
    # shellcheck disable=SC2206
    local matches=( $pattern )
    shopt -u nullglob
    for path in "${matches[@]}"; do
        if [ -r "$path" ] && [ -w "$path" ]; then
            return 0
        fi
    done
    return 1
}

print_device_nodes() {
    local label="$1"
    local directory="$2"
    if [ -d "$directory" ]; then
        echo "  $label:"
        ls -la "$directory" || true
    else
        echo "  $label not found."
    fi
}

echo "=========================================="
echo "  Experimental Linux hardware diagnostics"
echo "=========================================="
echo
echo "Linux support is experimental and currently supports Ubuntu and Fedora."
echo "Detected platform: $PLATFORM_NAME ($(detect_linux_pretty_name))"
echo "CPU should be the first Linux validation path."
echo "GPU/NPU require compatible Intel Linux drivers and may need extra system packages."
echo

echo "/etc/os-release:"
if [ -r /etc/os-release ]; then
    sed -n '1,80p' /etc/os-release
else
    echo "  not available"
fi
echo

echo "uname -a:"
uname -a || true
echo

echo "Kernel version:"
uname -r || true
echo

echo "CPU architecture:"
uname -m || true
if command -v lscpu >/dev/null 2>&1; then
    lscpu | sed -n '1,20p' || true
fi
echo

PYTHON_BIN=""
if [ -x "$VENV_PYTHON" ]; then
    PYTHON_BIN="$VENV_PYTHON"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3)"
fi

echo "Python:"
if [ -n "$PYTHON_BIN" ]; then
    "$PYTHON_BIN" --version || true
else
    echo "  python3 not found"
fi
echo

echo "Intel accelerator device nodes:"
print_device_nodes "/dev/dri" "/dev/dri"
print_device_nodes "/dev/accel" "/dev/accel"

echo "  Access checks:"
if has_rw_device '/dev/dri/renderD*'; then
    echo "    GPU render node: readable/writable by current user"
elif [ -d /dev/dri ]; then
    echo "    WARNING: no readable/writable /dev/dri/renderD* node for current user"
else
    echo "    GPU render node: not present"
fi
if has_rw_device '/dev/accel/accel*'; then
    echo "    NPU accel node: readable/writable by current user"
elif [ -d /dev/accel ]; then
    echo "    WARNING: no readable/writable /dev/accel/accel* node for current user"
else
    echo "    NPU accel node: not present"
fi

GROUPS_TEXT="$(id -nG 2>/dev/null || true)"
echo "  Current user groups: ${GROUPS_TEXT:-unknown}"
if printf '%s\n' "$GROUPS_TEXT" | tr ' ' '\n' | grep -Eq '^(render|video)$'; then
    echo "  accelerator group: current user is in render and/or video"
elif ! has_rw_device '/dev/dri/renderD*' && ! has_rw_device '/dev/accel/accel*'; then
    echo "  WARNING: current user is not in render/video and has no accelerator device access."
    CURRENT_USER="${USER:-${LOGNAME:-$(id -un 2>/dev/null || printf user)}}"
    echo "  Guidance only: sudo usermod -aG render,video \"$CURRENT_USER\""
    echo "  Log out and back in after changing group membership."
else
    echo "  accelerator group: render/video membership not detected, but device access is already available"
fi
echo

echo "Relevant kernel modules:"
if command -v lsmod >/dev/null 2>&1; then
    MODULES="$(lsmod 2>/dev/null | awk 'NR == 1 || $1 ~ /^(xe|i915|intel_vpu|ivpu)$/ {print}' || true)"
    if [ -n "$MODULES" ]; then
        printf '%s\n' "$MODULES"
    else
        echo "  no xe, i915, intel_vpu, or ivpu module reported by lsmod"
    fi
else
    echo "  lsmod not available"
fi
echo

PCI_OUTPUT=""
if command -v lspci >/dev/null 2>&1; then
    PCI_OUTPUT="$(lspci 2>/dev/null || true)"
    echo "lspci GPU/display entries:"
    if ! printf '%s\n' "$PCI_OUTPUT" | grep -Ei 'VGA|3D|Display' | grep -Ei 'Intel|ARC|Graphics|Xe' ; then
        echo "  no Intel GPU/display entries found by lspci"
    fi
    echo

    echo "lspci NPU/VPU/AI Boost hints:"
    if printf '%s\n' "$PCI_OUTPUT" | grep -Ei 'NPU|VPU|AI Boost|Neural|Gaussian|GNA' ; then
        echo "  NPU-like PCI entries are hints only. OpenVINO must list NPU before the app can use it."
    else
        echo "  no NPU/VPU/AI Boost hints found by lspci"
    fi
else
    echo "lspci: not installed."
    print_pciutils_hint "$OS_ID"
fi
echo

echo "OpenVINO import and device discovery:"
if [ -n "$PYTHON_BIN" ]; then
    "$PYTHON_BIN" - <<'PY'
import importlib.util

has_openvino = importlib.util.find_spec("openvino") is not None
has_genai = importlib.util.find_spec("openvino_genai") is not None
print(f"  openvino importable: {has_openvino}")
print(f"  openvino_genai importable: {has_genai}")

if has_openvino:
    try:
        import openvino as ov

        core = ov.Core()
        devices = list(core.available_devices)
        print("  OpenVINO available devices: " + (", ".join(devices) if devices else "(none detected)"))
        for device in devices:
            try:
                full_name = core.get_property(device, "FULL_DEVICE_NAME")
            except Exception:
                full_name = device
            try:
                driver = core.get_property(device, "DRIVER_VERSION")
            except Exception:
                driver = None
            suffix = f"; driver={driver}" if driver else ""
            print(f"    {device}: {full_name}{suffix}")
    except Exception as exc:
        print(f"  OpenVINO device discovery failed: {exc}")
else:
    print("  Install requirements.txt before expecting OpenVINO device discovery.")
PY
else
    echo "  Skipped: python3 not found"
fi
echo

echo "Notes:"
echo "  - CPU should work once Python/OpenVINO packages install."
echo "  - GPU requires Intel's Linux GPU runtime/driver stack and usable /dev/dri render nodes."
echo "  - NPU requires Intel's Linux NPU driver, supported hardware/kernel, and usable /dev/accel nodes."
echo "  - PCI visibility alone does not prove OpenVINO can use an accelerator."
echo "  - If OpenVINO does not list GPU or NPU, InferBridge cannot target that device."
echo "  - This script prints sudo commands only as guidance and does not run them."
