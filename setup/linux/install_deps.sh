#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/../.." >/dev/null 2>&1 && pwd)"
VENV_DIR="$REPO_ROOT/.venv"
VENV_PYTHON="$VENV_DIR/bin/python"
WITH_CONVERT=0
PYTHON_CMD=""

# shellcheck disable=SC1091
. "$SCRIPT_DIR/lib.sh"
OS_ID="$(detect_linux_id)"
PLATFORM_NAME="$(linux_platform_name "$OS_ID")"

usage() {
    cat <<'EOF'
Install Python dependencies for InferBridge on Linux.

Usage:
  setup/linux/install_deps.sh [options]
  setup/install_deps.sh [options]        # compatibility wrapper

Options:
  --with-convert       Also install requirements-convert.txt if present.
  --minimal            Runtime dependencies only (default).
  --python <command>   Use a specific Python command, for example python3.11.
  -h, --help           Show this help.
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --with-convert)
            WITH_CONVERT=1
            shift
            ;;
        --minimal)
            WITH_CONVERT=0
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

python_is_supported() {
    "$1" - <<'PY'
import sys

major, minor = sys.version_info[:2]
raise SystemExit(0 if major == 3 and 11 <= minor <= 13 else 1)
PY
}

resolve_python() {
    local candidate resolved
    local candidates=()

    if [ -n "$PYTHON_CMD" ]; then
        candidates+=("$PYTHON_CMD")
    fi
    candidates+=(python3.11 python3.12 python3.13 python3)

    for candidate in "${candidates[@]}"; do
        [ -n "$candidate" ] || continue
        resolved=""
        if [ -x "$candidate" ]; then
            resolved="$candidate"
        else
            resolved="$(command -v "$candidate" 2>/dev/null || true)"
        fi
        if [ -n "$resolved" ] && python_is_supported "$resolved"; then
            printf '%s\n' "$resolved"
            return 0
        fi
    done

    echo "ERROR: Python 3.11, 3.12, or 3.13 was not found." >&2
    if command -v python3 >/dev/null 2>&1; then
        echo "Detected python3: $(python3 --version 2>&1)" >&2
    fi
    print_supported_python_hint "$OS_ID"
    return 1
}

if ! linux_is_supported_distro "$OS_ID"; then
    echo "WARNING: Linux setup is tested on Ubuntu and Fedora. Continuing best-effort on $PLATFORM_NAME." >&2
fi

PYTHON_BIN="$(resolve_python)"
echo "Using Python: $($PYTHON_BIN --version 2>&1) ($PYTHON_BIN)"

if [ -d "$VENV_DIR" ] && [ ! -x "$VENV_PYTHON" ]; then
    echo "ERROR: $VENV_DIR exists but $VENV_PYTHON is missing." >&2
    echo "Remove or recreate the existing virtual environment, then run setup again." >&2
    exit 1
fi

if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtual environment at $VENV_DIR ..."
    if ! "$PYTHON_BIN" -m venv "$VENV_DIR"; then
        echo "ERROR: Failed to create virtual environment." >&2
        print_venv_install_hint "$OS_ID"
        exit 1
    fi
else
    echo "Using existing virtual environment at $VENV_DIR"
fi

echo "Upgrading pip ..."
"$VENV_PYTHON" -m pip install --upgrade pip

echo "Installing runtime dependencies (requirements.txt) ..."
"$VENV_PYTHON" -m pip install -r "$REPO_ROOT/requirements.txt"

if [ "$WITH_CONVERT" -eq 1 ]; then
    if [ -f "$REPO_ROOT/requirements-convert.txt" ]; then
        echo "Installing conversion dependencies (requirements-convert.txt) ..."
        "$VENV_PYTHON" -m pip install -r "$REPO_ROOT/requirements-convert.txt"
    else
        echo "requirements-convert.txt not found; skipping conversion dependencies."
    fi
else
    echo "Skipping conversion dependencies (--minimal)."
fi

printf 'installed\n' > "$REPO_ROOT/.deps_installed"
echo "Dependencies installed."
