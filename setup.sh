#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
REPO_ROOT="$SCRIPT_DIR"
LINUX_SETUP_DIR="$REPO_ROOT/setup/linux"

MINIMAL=0
SKIP_HARDWARE_CHECK=0
PYTHON_CMD=""

# shellcheck disable=SC1091
. "$LINUX_SETUP_DIR/lib.sh"
OS_ID="$(detect_linux_id)"
PLATFORM_NAME="$(linux_platform_name "$OS_ID")"

usage() {
    cat <<'EOF'
InferBridge - Experimental Linux Setup

Usage:
  ./setup.sh [options]

Options:
  --minimal              Install runtime dependencies only.
  --skip-hardware-check  Skip Linux hardware diagnostics and OpenVINO device check.
  --python <command>     Use a specific Python command, for example python3.11.
  -h, --help             Show this help.

Examples:
  ./setup.sh
  ./setup.sh --minimal
  ./setup.sh --skip-hardware-check
  ./setup.sh --python python3.11
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --minimal)
            MINIMAL=1
            shift
            ;;
        --skip-hardware-check)
            SKIP_HARDWARE_CHECK=1
            shift
            ;;
        --python)
            if [ "$#" -lt 2 ]; then
                echo "ERROR: --python requires a command." >&2
                exit 1
            fi
            PYTHON_CMD="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "ERROR: Unknown option: $1" >&2
            usage >&2
            exit 1
            ;;
    esac
done

echo "=========================================="
echo "  InferBridge - Linux Setup"
echo "=========================================="
echo
echo "Linux support is experimental and currently supports Ubuntu and Fedora."
echo
echo "Repo: $REPO_ROOT"

if [ -r /etc/os-release ]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    echo "OS: ${PRETTY_NAME:-unknown}"
    if ! linux_is_supported_distro "${ID:-unknown}"; then
        echo "WARNING: This script is tested on Ubuntu and Fedora. Continuing best-effort."
    fi
else
    echo "WARNING: /etc/os-release was not found. Continuing best-effort."
fi

# An explicit --python command is authoritative. Do not reject a valid custom
# interpreter just because the distro does not provide a `python3` convenience link.
if [ -z "$PYTHON_CMD" ] && ! command -v python3 >/dev/null 2>&1; then
    echo "ERROR: python3 was not found." >&2
    print_python_install_hint "$OS_ID"
    exit 1
fi

INSTALL_ARGS=()
if [ "$MINIMAL" -eq 0 ]; then
    INSTALL_ARGS+=(--with-convert)
fi
if [ -n "$PYTHON_CMD" ]; then
    INSTALL_ARGS+=(--python "$PYTHON_CMD")
fi

"$LINUX_SETUP_DIR/install_deps.sh" "${INSTALL_ARGS[@]}"

ENV_FILE="$REPO_ROOT/.env"
ENV_EXAMPLE_FILE="$REPO_ROOT/.env.example"
if [ ! -f "$ENV_FILE" ] && [ -f "$ENV_EXAMPLE_FILE" ]; then
    cp "$ENV_EXAMPLE_FILE" "$ENV_FILE"
    chmod 600 "$ENV_FILE" 2>/dev/null || true
    echo "Created .env from .env.example"
fi

# Hugging Face CLI credentials are user-owned secrets. Never copy their contents
# into the repository's .env file. The runtime can consume an explicitly exported
# HF_TOKEN/HUGGING_FACE_HUB_TOKEN when gated-model access is required.
HF_TOKEN_FILE=""
if [ -n "${HF_HOME:-}" ]; then
    HF_TOKEN_FILE="${HF_HOME%/}/token"
elif [ -n "${XDG_CACHE_HOME:-}" ]; then
    HF_TOKEN_FILE="${XDG_CACHE_HOME%/}/huggingface/token"
elif [ -n "${HOME:-}" ]; then
    HF_TOKEN_FILE="${HOME%/}/.cache/huggingface/token"
fi
if [ -n "$HF_TOKEN_FILE" ] && [ -f "$HF_TOKEN_FILE" ]; then
    echo "Detected existing Hugging Face CLI credentials."
    echo "InferBridge does not copy Hugging Face tokens into .env."
    echo "For gated-model access, export HF_TOKEN before starting InferBridge."
fi

if [ "$SKIP_HARDWARE_CHECK" -eq 0 ]; then
    echo
    "$LINUX_SETUP_DIR/check_hardware.sh" || {
        echo "WARNING: Linux hardware diagnostics failed; continuing."
    }

    echo
    echo "OpenVINO device check:"
    if ! "$REPO_ROOT/.venv/bin/python" -m app.server --check-devices; then
        echo "WARNING: OpenVINO device check failed. CPU should be the first $PLATFORM_NAME validation path."
    fi
else
    echo "Skipping hardware diagnostics and OpenVINO device check."
fi

echo
echo "=========================================="
echo "  Setup complete"
echo "=========================================="
echo
echo "Next steps:"
echo "  1. Verify the app stack without a real model:"
echo "       ./start_server.sh --mock"
echo "  2. Convert a small catalog model when conversion dependencies are installed:"
echo "       ./setup/linux/convert_model.sh --id tinyllama-1.1b-chat-fp16"
echo "  3. Start with CPU first on Linux:"
echo "       ./start_server.sh --model tinyllama-1.1b-chat-fp16 --device CPU"
echo
echo "Linux GPU/NPU use is experimental and depends on compatible Intel Linux drivers."
