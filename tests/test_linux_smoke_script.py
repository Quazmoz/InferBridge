"""Exercise the Linux smoke harness without installing inference dependencies."""

import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "setup/linux/smoke_test.sh"
pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux source launcher")


def _checkout(tmp_path):
    root = tmp_path / "checkout with spaces"
    (root / "setup/linux").mkdir(parents=True)
    (root / "scripts").mkdir()
    (root / ".venv/bin").mkdir(parents=True)
    shutil.copyfile(SCRIPT, root / "setup/linux/smoke_test.sh")
    (root / ".venv/bin/python").symlink_to(sys.executable)
    (root / ".deps_installed").write_text("installed\n")
    (root / "models.json").write_text("{}\n")
    (root / "start_server.sh").write_text(
        "#!/bin/bash\n"
        'exec "$(dirname "$0")/.venv/bin/python" - "$@" <<\'PY\'\n'
        "import http.server, os, pathlib, sys\n"
        "port = int(sys.argv[sys.argv.index('--port') + 1])\n"
        "pathlib.Path(os.environ['STUB_PID_MARKER']).write_text(str(os.getpid()))\n"
        "class Handler(http.server.BaseHTTPRequestHandler):\n"
        " def do_GET(self):\n"
        "  self.send_response(200 if self.path == '/health/live' else 404)\n"
        "  self.end_headers()\n"
        " def log_message(self, *args): pass\n"
        "http.server.HTTPServer(('127.0.0.1', port), Handler).serve_forever()\n"
        "PY\n",
        encoding="utf-8",
    )
    (root / "start_server.sh").chmod(0o755)
    (root / "scripts/validate_api_contract.py").write_text(
        "import os, pathlib, urllib.request, sys\n"
        "assert os.environ['OV_LLM_MOCK'] == '1'\n"
        "assert os.environ['OV_LLM_HOST'] == '127.0.0.1'\n"
        "assert os.environ['OV_LLM_API_KEY'] == ''\n"
        "assert 'inferbridge-linux-smoke.' in os.environ['OV_LLM_DATA_DIR']\n"
        "assert os.environ['OV_LLM_MODELS_FILE'].startswith(os.path.dirname(os.environ['OV_LLM_DATA_DIR']))\n"
        "assert os.environ['OV_LLM_AUTO_CONVERT'] == '0'\n"
        "assert 'HF_TOKEN' not in os.environ\n"
        "assert '--expect-mock' in sys.argv and '--exercise-lifecycle' in sys.argv\n"
        "base = sys.argv[sys.argv.index('--base-url') + 1]\n"
        "assert urllib.request.urlopen(base + '/health/live').status == 200\n"
        "pathlib.Path(os.environ['STUB_RESULT']).write_text('validated')\n",
        encoding="utf-8",
    )
    return root


def _free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _run(root, *args, env=None):
    return subprocess.run(
        ["bash", str(root / "setup/linux/smoke_test.sh"), *args],
        cwd=root,
        env={**os.environ, **(env or {})},
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )


def test_linux_smoke_exercises_owned_loopback_server_and_isolates_data(tmp_path):
    root = _checkout(tmp_path)
    marker = tmp_path / "validated"
    pid_marker = tmp_path / "server.pid"
    result = _run(
        root,
        "--port",
        str(_free_port()),
        env={
            "STUB_RESULT": str(marker),
            "STUB_PID_MARKER": str(pid_marker),
            "OV_LLM_HOST": "0.0.0.0",
            "OV_LLM_API_KEY": "unexpected-secret",
            "OV_LLM_DATA_DIR": str(tmp_path / "do-not-touch"),
            "OV_LLM_MODELS_FILE": str(tmp_path / "do-not-touch" / "models.json"),
            "OV_LLM_AUTO_CONVERT": "1",
            "HF_TOKEN": "not-a-real-token",
        },
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert marker.read_text() == "validated"
    assert "Linux source launcher and mock API contract passed." in result.stdout
    assert not (tmp_path / "do-not-touch").exists()
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_marker.read_text()), 0)


@pytest.mark.parametrize("bad_port", ["0", "80", "70000", "oops", "8124; echo bad"])
def test_linux_smoke_rejects_invalid_ports_before_starting(tmp_path, bad_port):
    root = _checkout(tmp_path)
    result = _run(root, "--port", bad_port)
    assert result.returncode == 2
    assert "unprivileged TCP port" in result.stderr


def test_linux_smoke_refuses_occupied_port(tmp_path):
    root = _checkout(tmp_path)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        result = _run(root, "--port", str(port))
    assert result.returncode == 1
    assert "is not available" in result.stderr
