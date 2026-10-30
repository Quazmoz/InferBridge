from __future__ import annotations

import importlib.util
import json
import os
import shutil
import signal
import subprocess
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


def test_user_service_install_collision_and_uninstall(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config λ"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / 'data $user % "λ"'))
    monkeypatch.delenv("OV_LLM_DATA_DIR", raising=False)
    old, new = tmp_path / "old bundle", tmp_path / "new bundle"
    target = tmp_path / "config λ/systemd/user/inferbridge.service"
    assert desktop.integrate_service(old) == 0
    original = target.read_text(encoding="utf-8")
    assert "--service" in original
    assert "Restart=on-failure" in original and "KillMode=mixed" in original
    assert desktop.integrate_service(old) == 0
    assert desktop.integrate_service(new) == 2
    assert target.read_text(encoding="utf-8") == original
    desktop.integrate_service(new, remove=True)
    assert target.is_file()
    desktop.integrate_service(old, remove=True)
    assert not target.exists()
    assert not list(target.parent.glob(".*"))


def test_service_preserves_custom_unit_and_uses_absolute_xdg_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", "relative")
    monkeypatch.setenv("XDG_DATA_HOME", "relative")
    monkeypatch.delenv("OV_LLM_DATA_DIR", raising=False)
    root = tmp_path / "bundle"
    desktop.integrate_service(root)
    target = tmp_path / ".config/systemd/user/inferbridge.service"
    environment = next(
        line.split("=", 1)[1]
        for line in target.read_text().splitlines()
        if line.startswith("Environment=")
    )
    assert json.loads(environment) == "XDG_DATA_HOME=" + str(tmp_path / ".local/share")
    custom = target.read_text() + "# user's changes\n"
    target.write_text(custom)
    assert desktop.integrate_service(root) == 2
    desktop.integrate_service(root, remove=True)
    assert target.read_text() == custom


def test_entry_write_failure_preserves_existing_file_and_removes_staging_file(
    monkeypatch, tmp_path
):
    target = tmp_path / "inferbridge.desktop"
    target.write_text("original")

    def fail_replace(_source, _destination):
        raise PermissionError("read-only destination")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(PermissionError):
        desktop.write_entry(target, "replacement")
    assert target.read_text() == "original"
    assert list(tmp_path.iterdir()) == [target]


def test_service_unit_escapes_systemd_paths_and_preserves_data_override(monkeypatch, tmp_path):
    monkeypatch.setenv("OV_LLM_DATA_DIR", str(tmp_path / 'data %h ${HOME} "λ"'))
    unit = desktop.service_entry(tmp_path / 'bundle %h ${HOME} "λ" \\')
    assert 'ExecStart=:/bin/bash -- "' in unit  # A safe executable; the bundle is an argument.
    assert "%%h" in unit and "${HOME}" in unit and '\\"λ\\"' in unit
    assert " --data-dir " in unit and "data %%h" in unit
    for control in ("\n", "\r", "\t", "\0", "\x7f"):
        with pytest.raises(ValueError):
            desktop.service_entry(tmp_path / f"bad{control}path")


@pytest.mark.skipif(
    sys.platform != "linux" or not shutil.which("systemd-analyze"),
    reason="Linux systemd unit parser",
)
def test_service_unit_passes_systemd_parser(monkeypatch, tmp_path):
    monkeypatch.delenv("OV_LLM_DATA_DIR", raising=False)
    root = tmp_path / 'bundle %h ${HOME} "λ" \\'
    root.mkdir()
    executable = root / "InferBridge"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o755)
    target = tmp_path / "inferbridge.service"
    target.write_text(desktop.service_entry(root))
    subprocess.run(["systemd-analyze", "--user", "verify", str(target)], check=True, timeout=20)


def test_service_cli_requires_headless_exclusive_owner(monkeypatch):
    import app.tray_app

    captured = []
    monkeypatch.setattr(
        app.tray_app, "run_tray_controller", lambda args: captured.append(args) or 0
    )
    assert main(["--service"]) == 0
    assert captured[0].headless and captured[0].no_browser and captured[0].service


def test_service_refuses_an_existing_desktop_owner(capsys):
    application = TrayRuntimeMixin()
    application.args = SimpleNamespace(service=True, quit=False)
    application.lock = SimpleNamespace(acquire=lambda: False)
    assert application.run() == 6
    assert "Another application instance" in capsys.readouterr().err


@pytest.mark.parametrize(
    "headless,stopping,expected", [(True, False, 6), (True, True, 0), (False, False, 0)]
)
def test_headless_backend_failure_is_visible_but_intentional_quit_is_clean(
    capsys, headless, stopping, expected
):
    application = TrayRuntimeMixin()
    application.args = SimpleNamespace(headless=headless, headless_seconds=0)
    application.stop_event = threading.Event()
    application.controller = SimpleNamespace(
        running=False, last_error="Server exited with code 17."
    )

    def poll():
        if stopping or not headless:
            application.stop_event.set()

    application._poll_once = poll
    assert application._run_headless() == expected
    assert ("headless backend failed" in capsys.readouterr().err) == (expected == 6)


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


def test_linux_constraints_stay_aligned_with_retained_hash_lock():
    # Parse extras as part of the same distribution name: uvicorn[standard]
    # and optimum-intel[openvino] must not silently escape this contract.
    import re

    def pins(contents):
        return {
            name.lower().replace("_", "-"): version
            for name, version in re.findall(
                r"^([A-Za-z0-9_.-]+)(?:\[[^\]]+\])?==([^\s;\\]+)",
                contents,
                re.MULTILINE,
            )
        }

    source = (ROOT / "requirements/linux.in").read_text(encoding="utf-8")
    assert "-c linux-baseline.txt" in source
    baseline = pins((ROOT / "requirements/linux-baseline.txt").read_text(encoding="utf-8"))
    locked = pins((ROOT / "requirements/linux.lock").read_text(encoding="utf-8"))
    requested = {
        line.strip().split("[", 1)[0].lower().replace("_", "-")
        for line in source.splitlines()
        if line.strip() and not line.lstrip().startswith(("#", "-"))
    }
    assert {"uvicorn", "optimum-intel"} <= requested
    assert requested <= locked.keys()
    for name, expected in baseline.items():
        if name in locked:
            assert locked[name].split("+", 1)[0] == expected, name


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
