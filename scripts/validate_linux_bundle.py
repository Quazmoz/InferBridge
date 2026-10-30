"""Exercise an extracted Linux application, never importing application source here."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def wait_for(predicate, *, timeout: float = 60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.1)
    raise RuntimeError("Packaged application check exceeded its deadline.")


def validate(bundle: Path, evidence: Path) -> None:
    evidence.mkdir(parents=True, exist_ok=True)
    executable = bundle / "InferBridge"
    assert executable.is_file() and os.access(executable, os.X_OK)
    assert not (bundle / "data").exists()
    assert not any(
        path.name in {".env", "desktop-instance.json", "tray-command.json"}
        for path in (bundle / "resources").rglob("*")
    )
    assert not any(bundle.rglob("*.safetensors")), (
        "A distribution must not contain downloaded model weights"
    )
    before = {
        str(p.relative_to(bundle)): p.stat().st_mtime_ns for p in bundle.rglob("*") if p.is_file()
    }
    with tempfile.TemporaryDirectory(prefix="inferbridge-user-") as temporary:
        work = Path(temporary)
        data = work / "user data λ"
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("OV_LLM_", "HF_", "HUGGING", "PYTHON"))
        }
        for key in ("LD_LIBRARY_PATH", "LD_PRELOAD", "LD_AUDIT", "VIRTUAL_ENV", "CONDA_PREFIX"):
            environment.pop(key, None)
        environment.update(
            {
                "XDG_DATA_HOME": str(work / "xdg"),
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
                "HF_HUB_DISABLE_TELEMETRY": "1",
                "DO_NOT_TRACK": "1",
                "OV_LLM_API_KEY": secrets.token_hex(24),
            }
        )

        def run(*arguments, timeout=90, check=True):
            result = subprocess.run(
                [str(executable), *arguments],
                cwd=work,
                env=environment,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            if check and result.returncode:
                print(result.stdout[-4000:] + result.stderr[-4000:], file=sys.stderr)
                result.check_returncode()
            return result

        run("--bootstrap-smoke")
        run("--native-smoke", timeout=180)
        conversion = subprocess.run(
            [str(bundle / "python/bin/optimum-cli"), "export", "openvino", "--help"],
            cwd=work,
            env={
                **environment,
                "PYTHONPATH": str(bundle / "resources"),
                "PYTHONSAFEPATH": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
            },
            capture_output=True,
            text=True,
            timeout=180,
            check=True,
        )
        assert "--model" in conversion.stdout
        run("--install-desktop")
        entries = list((work / "xdg" / "applications").glob("*.desktop"))
        assert len(entries) == 2
        for entry in entries:
            subprocess.run(["desktop-file-validate", str(entry)], check=True, timeout=10)
        run("--uninstall-desktop")
        assert not any((work / "xdg" / "applications").glob("*.desktop"))
        # A foreign listener must remain untouched, and a fixed-port launch must fail.
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            occupied = listener.getsockname()[1]
            failed = run(
                "--mock",
                "--headless",
                "--no-browser",
                "--data-dir",
                str(data),
                "--port",
                str(occupied),
                check=False,
            )
            assert failed.returncode != 0
        (data / "models").mkdir(parents=True, exist_ok=True)
        sentinel = data / "models" / "user-owned.txt"
        sentinel.write_text("preserve me", encoding="utf-8")
        metadata_file = data / "desktop-instance.json"
        command = [str(executable), "--mock", "--headless", "--no-browser", "--data-dir", str(data)]
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

        def request(metadata, path, *, key=True, method="GET"):
            headers = {"Authorization": f"Bearer {environment['OV_LLM_API_KEY']}"} if key else {}
            if method == "POST":
                headers.update({"X-OV-LLM-UI": "1", "Content-Type": "application/json"})
            req = urllib.request.Request(
                f"http://127.0.0.1:{metadata['port']}{path}",
                headers=headers,
                method=method,
                data=b"{}" if method == "POST" else None,
            )
            with opener.open(req, timeout=2) as response:
                return response.read()

        def ready():
            try:
                metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
                instance = json.loads(request(metadata, "/desktop/instance"))
                return metadata if instance.get("instance_nonce") == metadata["nonce"] else None
            except (OSError, ValueError, urllib.error.URLError):
                return None

        processes = []
        with (evidence / "launcher.log").open("w", encoding="utf-8") as log:
            try:
                owner = subprocess.Popen(command, cwd=work, env=environment, stdout=log, stderr=log)
                processes.append(owner)
                metadata = wait_for(ready)
                run(*command[1:])
                assert ready()["pid"] == metadata["pid"], (
                    "Duplicate launch created a competing server"
                )
                assert b"InferBridge" in request(metadata, "/")
                try:
                    request(metadata, "/v1/models", key=False)
                except urllib.error.HTTPError as error:
                    assert error.code == 401
                else:
                    raise AssertionError("Unauthenticated API request was accepted")
                subprocess.run(
                    [
                        sys.executable,
                        str(ROOT / "scripts" / "validate_api_contract.py"),
                        "--base-url",
                        f"http://127.0.0.1:{metadata['port']}",
                        "--profile",
                        "full",
                        "--device",
                        "CPU",
                        "--expect-mock",
                        "--include-embeddings",
                        "--run-benchmark",
                        "--exercise-lifecycle",
                        "--load-timeout",
                        "60",
                        "--timeout",
                        "30",
                        "--output-json",
                        str(evidence / "contract.json"),
                        "--output-markdown",
                        str(evidence / "contract.md"),
                    ],
                    check=True,
                    timeout=240,
                    cwd=work,
                    env=environment,
                )
                request(metadata, "/v1/desktop/operations/restart-server", method="POST")

                def restarted():
                    current = ready()
                    return current if current and current["nonce"] != metadata["nonce"] else None

                wait_for(restarted)
                assert owner.poll() is None
                run("--quit", "--data-dir", str(data))
                assert owner.wait(timeout=45) == 0
                assert not metadata_file.exists()
                for signum in (signal.SIGINT, signal.SIGTERM):
                    owner = subprocess.Popen(
                        command, cwd=work, env=environment, stdout=log, stderr=log
                    )
                    processes.append(owner)
                    wait_for(ready)
                    owner.send_signal(signum)
                    assert owner.wait(timeout=45) == 0
                    assert sentinel.read_text(encoding="utf-8") == "preserve me"
                # SIGKILL bypasses cleanup; the server owner monitor must reap the orphan.
                owner = subprocess.Popen(command, cwd=work, env=environment, stdout=log, stderr=log)
                processes.append(owner)
                stale = wait_for(ready)
                owner.kill()
                owner.wait(timeout=10)

                def orphan_stopped():
                    try:
                        request(stale, "/health/live")
                    except (OSError, urllib.error.URLError):
                        return True
                    return False

                wait_for(orphan_stopped, timeout=35)
                owner = subprocess.Popen(command, cwd=work, env=environment, stdout=log, stderr=log)
                processes.append(owner)
                recovered = wait_for(ready)
                assert recovered["nonce"] != stale["nonce"]
                run("--quit", "--data-dir", str(data))
                assert owner.wait(timeout=45) == 0
                assert sentinel.read_text(encoding="utf-8") == "preserve me"
            finally:
                for process in processes:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=45)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=10)
    after = {
        str(p.relative_to(bundle)): p.stat().st_mtime_ns for p in bundle.rglob("*") if p.is_file()
    }
    assert after == before, "The application wrote inside its installation directory"
    (evidence / "packaged-checks.json").write_text(
        json.dumps(
            {
                "platform": sys.platform,
                "architecture": os.uname().machine,
                "mock": True,
                "real_inference": False,
                "passed": True,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    print("Packaged Linux lifecycle, resources, authentication and mock API checks passed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="inferbridge-relocated-λ ") as directory:
        with tarfile.open(args.archive) as archive:
            archive.extractall(directory, filter="data")
        bundles = list(Path(directory).iterdir())
        assert len(bundles) == 1 and bundles[0].is_dir()
        validate(bundles[0], args.evidence.resolve())
