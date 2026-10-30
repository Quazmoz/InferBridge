from __future__ import annotations

import importlib.util
import json
import os
import signal
import sys
import tarfile
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import frozen_entrypoint
from app.desktop_launcher import main
from app.tray_runtime import TrayRuntimeMixin
from scripts.build_linux_distribution import PYTHON, archive_bundle, managed_installation

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "linux_desktop", ROOT / "packaging/linux/linux_desktop.py"
)
desktop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(desktop)


def test_managed_interpreter_ignores_uv_minor_version_alias(tmp_path):
    runtime = tmp_path / f"cpython-{PYTHON}-linux-x86_64-gnu"
    runtime.mkdir()
    (tmp_path / "cpython-3.12-linux-x86_64-gnu").symlink_to(runtime, target_is_directory=True)
    assert managed_installation(tmp_path) == runtime


@pytest.mark.skipif(os.name == "nt", reason="POSIX resource measurement")
def test_real_probe_rejects_unbounded_and_pickle_only_downloads():
    from scripts.validate_linux_cpu import selected_files

    assert selected_files(
        [
            SimpleNamespace(rfilename="model.safetensors", size=123),
            SimpleNamespace(rfilename="pytorch_model.bin", size=999),
        ]
    ) == ["model.safetensors"]
    for files in (
        [SimpleNamespace(rfilename="model.safetensors", size=None)],
        [SimpleNamespace(rfilename="model.safetensors", size=17 * 1024 * 1024)],
        [SimpleNamespace(rfilename="pytorch_model.bin", size=123)],
    ):
        with pytest.raises(RuntimeError):
            selected_files(files)


@pytest.mark.skipif(
    os.name == "nt", reason="Linux archive requires POSIX executable permission bits"
)
def test_archive_reproducibility_permissions_and_symlinks(tmp_path):
    bundle = tmp_path / "InferBridge-1.0.0-linux-x86_64"
    bundle.mkdir()
    executable = bundle / "InferBridge"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    (bundle / "link").symlink_to("InferBridge")
    first, second = tmp_path / "first.tgz", tmp_path / "second.tgz"
    archive_bundle(bundle, first, epoch=100)
    os.utime(executable, (200, 200))
    archive_bundle(bundle, second, epoch=100)
    assert first.read_bytes() == second.read_bytes()
    with tarfile.open(first) as archive:
        assert archive.getmember(f"{bundle.name}/InferBridge").mode == 0o755
        assert archive.getmember(f"{bundle.name}/link").issym()
        assert all(member.uid == 0 and member.mtime == 100 for member in archive.getmembers())


def test_desktop_upgrade_and_uninstall_preserve_data(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    old = tmp_path / "old bundle λ"
    new = tmp_path / 'new $bundle "λ" %'
    user_model = tmp_path / "xdg" / "InferBridge" / "models" / "keep.bin"
    user_model.parent.mkdir(parents=True)
    user_model.write_bytes(b"user data")
    desktop.integrate(old)
    desktop.integrate(new)
    desktop.integrate(old, remove=True)
    entries = list((tmp_path / "xdg" / "applications").glob("*.desktop"))
    assert len(entries) == 2
    assert all("Exec=" in item.read_text() for item in entries)
    assert "%%" in entries[0].read_text()
    desktop.integrate(new, remove=True)
    assert not any((tmp_path / "xdg" / "applications").glob("*.desktop"))
    assert user_model.read_bytes() == b"user data"


def test_relative_xdg_desktop_path_uses_home(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", "relative")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    desktop.integrate(tmp_path / "bundle")
    assert (tmp_path / ".local/share/applications/inferbridge.desktop").is_file()


def test_desktop_entry_rejects_newline_injection():
    with pytest.raises(ValueError):
        desktop.desktop_entry(Path("/tmp/injected\nExec=bad"))


def test_linux_frozen_entrypoint_uses_existing_controller(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setitem(
        sys.modules,
        "app.desktop_launcher",
        SimpleNamespace(main=lambda args: 17 if args == [] else 2),
    )
    assert frozen_entrypoint.run([]) == 17


def test_data_directory_environment_override_reaches_controller(monkeypatch, tmp_path):
    import app.tray_app

    monkeypatch.setenv("OV_LLM_DATA_DIR", str(tmp_path / "data λ"))
    monkeypatch.setattr(
        app.tray_app,
        "run_tray_controller",
        lambda args: 19 if args.data_dir == str(tmp_path / "data λ") else 2,
    )
    assert main(["--mock", "--no-browser"]) == 19


def test_quit_reuses_owner_command_and_waits_for_lock(tmp_path):
    class Lock:
        attempts = 0

        def acquire(self):
            self.attempts += 1
            return self.attempts > 1

        def release(self):
            self.released = True

    application = TrayRuntimeMixin()
    application.args = SimpleNamespace(quit=True)
    application.lock = Lock()
    application.command_file = tmp_path / "tray-command.json"
    assert application.run() == 0
    assert json.loads(application.command_file.read_text())["command"] == "quit"
    assert application.lock.released


def test_quit_is_not_lost_when_startup_clears_stale_commands(tmp_path):
    command = tmp_path / "tray-command.json"

    class Lock:
        attempts = 0

        def acquire(self):
            self.attempts += 1
            if self.attempts == 2:
                command.unlink()
            if self.attempts == 3:
                assert json.loads(command.read_text())["command"] == "quit"
                return True
            return False

        def release(self):
            pass

    application = TrayRuntimeMixin()
    application.args = SimpleNamespace(quit=True)
    application.lock = Lock()
    application.command_file = command
    assert application.run() == 0


@pytest.mark.skipif(os.name == "nt", reason="POSIX signal boundary")
def test_linux_signal_requests_cleanup_and_restores_handlers(monkeypatch):
    import app.tray_app as tray_app

    class Application:
        def __init__(self, args):
            self.stop_event = threading.Event()
            self.icon = None

        def run(self):
            os.kill(os.getpid(), signal.SIGTERM)
            assert self.stop_event.is_set()
            return 0

    previous = signal.getsignal(signal.SIGTERM)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(tray_app, "TrayApplication", Application)
    assert tray_app.run_tray_controller(SimpleNamespace()) == 0
    assert signal.getsignal(signal.SIGTERM) == previous


def test_linux_lock_contains_cpu_torch_and_no_cuda():
    lock = (ROOT / "requirements/linux.lock").read_text()
    assert "torch==2.12.0+cpu" in lock
    assert "nvidia-" not in lock
    assert "openvino-genai==2026.2.1.0" in lock
    assert "--hash=sha256:" in lock


def test_packaged_device_report_does_not_invent_accelerators(monkeypatch, tmp_path, capsys):
    from runtime import device_check

    nodes = tmp_path / "dev"
    (nodes / "dri").mkdir(parents=True)
    (nodes / "dri/renderD128").touch()
    monkeypatch.setattr(device_check, "available_devices", lambda: ["CPU"])
    monkeypatch.setattr(device_check, "device_details", lambda: [{"device": "CPU"}])
    inspect = device_check.linux_device_nodes
    monkeypatch.setattr(device_check, "linux_device_nodes", lambda: inspect(nodes))
    monkeypatch.setattr(sys, "platform", "linux")
    assert main(["--check-devices"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["available"] == ["CPU"]
    assert len(report["linux_device_nodes"]["GPU"]) == 1
    assert report["linux_device_nodes"]["NPU"] == []
