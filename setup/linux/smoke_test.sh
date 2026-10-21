#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/../.." >/dev/null 2>&1 && pwd)"
PYTHON="$REPO_ROOT/.venv/bin/python"
PORT=8124

usage() {
    cat <<'HELP'
Validate the Linux source launcher and OpenAI-compatible mock API without model downloads.

Usage:
  bash ./setup/linux/smoke_test.sh [--port 8124]

Requires ./setup.sh --minimal and an unused loopback TCP port.
No GPU, NPU, Hugging Face token, or system-wide installation is required.
HELP
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --port)
            if [ "$#" -lt 2 ]; then
                echo 'ERROR: --port requires a TCP port.' >&2
                exit 2
            fi
            PORT="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "ERROR: Unknown argument: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if [[ ! "$PORT" =~ ^[0-9]{1,5}$ ]] || (( 10#$PORT < 1024 || 10#$PORT > 65535 )); then
    echo 'ERROR: --port must be an unprivileged TCP port (1024-65535).' >&2
    exit 2
fi
if [ ! -x "$PYTHON" ] || [ ! -f "$REPO_ROOT/.deps_installed" ]; then
    echo 'ERROR: Linux runtime setup incomplete; run ./setup.sh --minimal first.' >&2
    exit 1
fi

# Refuse to test an already-running service: a response from another process
# would not prove that the newly launched InferBridge instance works.
if ! "$PYTHON" - "$PORT" <<'PY'
import socket
import sys

with socket.socket() as listener:
    listener.bind(("127.0.0.1", int(sys.argv[1])))
PY
then
    echo "ERROR: 127.0.0.1:$PORT is not available; choose another --port." >&2
    exit 1
fi

WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/inferbridge-linux-smoke.XXXXXXXX")"
chmod 700 "$WORK_DIR"
SERVER_PID=""
cleanup() {
    result=$?
    trap - EXIT INT TERM
    if [ -n "$SERVER_PID" ]; then
        kill "$SERVER_PID" 2>/dev/null || true
        wait "$SERVER_PID" 2>/dev/null || true
    fi
    if [ "$result" -eq 0 ]; then
        rm -rf -- "$WORK_DIR"
    else
        echo "Linux smoke failed; private diagnostics retained at $WORK_DIR" >&2
        if [ -f "$WORK_DIR/server.log" ]; then
            tail -n 35 "$WORK_DIR/server.log" >&2 || true
        fi
    fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

cd "$REPO_ROOT"
cp -- "$REPO_ROOT/models.json" "$WORK_DIR/models.json"
# Override any checkout .env or inherited settings that could expose the
# service, use existing model data, or enable unexpected real inference.
export OV_LLM_MOCK=1
export OV_LLM_HOST=127.0.0.1
export OV_LLM_PORT="$PORT"
export OV_LLM_API_KEY=""
export OV_LLM_DATA_DIR="$WORK_DIR/data"
export OV_LLM_MODELS_FILE="$WORK_DIR/models.json"
export OV_LLM_AUTO_CONVERT=0
export OV_LLM_MODELS_DIR="$WORK_DIR/models"
export OV_LLM_CACHE_DIR="$WORK_DIR/cache"
export OV_LLM_BENCHMARK_RESULTS="$WORK_DIR/benchmarks.json"
unset HF_TOKEN HUGGING_FACE_HUB_TOKEN

"$REPO_ROOT/start_server.sh" --mock --host 127.0.0.1 --port "$PORT" \
    --model tinyllama-1.1b-chat-fp16 >"$WORK_DIR/server.log" 2>&1 &
SERVER_PID=$!

live=0
for ((attempt = 0; attempt < 45; attempt++)); do
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then
        echo 'ERROR: Linux launcher exited before its liveness probe responded.' >&2
        exit 1
    fi
    if "$PYTHON" - "$PORT" <<'PY' >/dev/null 2>&1
import sys
import urllib.request

with urllib.request.urlopen(
    f"http://127.0.0.1:{sys.argv[1]}/health/live", timeout=1
) as response:
    raise SystemExit(0 if response.status == 200 else 1)
PY
    then
        live=1
        break
    fi
    sleep 1
done
if [ "$live" -ne 1 ]; then
    echo 'ERROR: Linux launcher did not become live within 45 seconds.' >&2
    exit 1
fi

echo "Linux mock server is live on 127.0.0.1:$PORT; validating API contracts..."
"$PYTHON" scripts/validate_api_contract.py \
    --base-url "http://127.0.0.1:$PORT" \
    --profile full \
    --model tinyllama-1.1b-chat-fp16 \
    --device CPU \
    --expect-mock \
    --include-embeddings \
    --run-benchmark \
    --exercise-lifecycle \
    --startup-timeout 30 \
    --load-timeout 60 \
    --timeout 30 \
    --output-json "$WORK_DIR/contract.json" \
    --output-markdown "$WORK_DIR/contract.md"

echo 'Linux source launcher and mock API contract passed.'
