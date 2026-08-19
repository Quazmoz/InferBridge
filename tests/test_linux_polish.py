from __future__ import annotations

import os
import shutil
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import desktop_shell, paths as desktop_paths
from app.tray_runtime import TrayRuntimeMixin
from app.tray_state import TrayPhase, TraySnapshot, menu_state

ROOT = Path(__file__).resolve().parents[1]
LINUX_ONLY = pytest.mark.skipif(os.name == "nt", reason="Linux/Unix behavior")


@LINUX_ONLY
def test_desktop_paths_honor_absolute_xdg_data_home(monkeypatch, tmp_path):
    resource = tmp_path / "resources"
    resource.mkdir()
    monkeypatch.setattr(desktop_paths, "packaged_resource_root", lambda: resource)

    resolved = desktop_paths.resolve_runtime_paths(
        desktop=True,
        portable=False,
        env={
            "HOME": str(tmp_path / "home"),
            "XDG_DATA_HOME": str(tmp_path / "xdg-data"),
        },
    )

    assert resolved.data_root == (tmp_path / "xdg-data" / "InferBridge").resolve()


@LINUX_ONLY
def test_relative_xdg_data_home_is_ignored(monkeypatch, tmp_path):
    resource = tmp_path / "resources"
    resource.mkdir()
    monkeypatch.setattr(desktop_paths, "packaged_resource_root", lambda: resource)

    resolved = desktop_paths.resolve_runtime_paths(
        desktop=True,
        portable=False,
        env={"HOME": str(tmp_path / "home"), "XDG_DATA_HOME": "relative/data"},
    )

    assert resolved.data_root == (tmp_path / "home" / ".local" / "share" / "InferBridge").resolve()


@LINUX_ONLY
def test_linux_clipboard_prefers_wayland_wl_copy(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setattr(
        desktop_shell.shutil,
        "which",
        lambda command: f"/usr/bin/{command}" if command in {"wl-copy", "xclip"} else None,
    )

    def fake_run(command, **kwargs):
        calls.append(list(command))
        assert kwargs["input"] == b"hello"
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(desktop_shell.subprocess, "run", fake_run)

    desktop_shell.copy_to_clipboard("hello")

    assert calls == [["wl-copy", "--type", "text/plain;charset=utf-8"]]


@LINUX_ONLY
def test_open_path_falls_back_to_gio(monkeypatch, tmp_path):
    calls: list[list[str]] = []
    monkeypatch.setattr(
        desktop_shell.shutil,
        "which",
        lambda command: "/usr/bin/gio" if command == "gio" else None,
    )
    monkeypatch.setattr(
        desktop_shell,
        "_spawn_detached",
        lambda command: calls.append(list(command)) or True,
    )

    assert desktop_shell.open_path(tmp_path / "models") is True
    assert calls == [["gio", "open", str((tmp_path / "models").resolve())]]


@LINUX_ONLY
def test_non_windows_tray_does_not_offer_windows_startup_toggle(tmp_path):
    state = menu_state(
        TraySnapshot(phase=TrayPhase.READY, server_status="Ready", live=True, port=8000),
        models_dir=tmp_path / "models",
        logs_dir=tmp_path / "logs",
        diagnostics_dir=tmp_path / "diagnostics",
        portable=False,
    )

    assert state.start_with_windows is False


@LINUX_ONLY
def test_linux_tray_fallback_keeps_controller_loop_alive(monkeypatch):
    messages: list[str] = []

    class Stub(TrayRuntimeMixin):
        def __init__(self):
            self.args = SimpleNamespace(headless_seconds=0)
            self.stop_event = threading.Event()
            self.polls = 0

        def _poll_once(self):
            self.polls += 1
            self.stop_event.set()

    monkeypatch.setattr(
        "app.tray_runtime.show_dialog",
        lambda _title, message, **_kwargs: messages.append(message),
    )
    stub = Stub()

    assert stub._run_linux_without_tray("backend missing") == 0
    assert stub.polls == 1
    assert any("keep running without a tray icon" in message for message in messages)


def test_linux_setup_does_not_copy_hugging_face_token_into_env():
    script = (ROOT / "setup.sh").read_text(encoding="utf-8")

    assert "tr -d" not in script
    assert "awk -v token" not in script
    assert 'print "HF_TOKEN=" token' not in script
    assert "does not copy Hugging Face tokens into .env" in script
    assert 'if [ -z "$PYTHON_CMD" ] && ! command -v python3' in script


@LINUX_ONLY
def test_linux_hardware_check_covers_gpu_and_npu_device_nodes():
    script = (ROOT / "setup" / "linux" / "check_hardware.sh").read_text(encoding="utf-8")

    assert "/dev/dri/renderD*" in script
    assert "/dev/accel/accel*" in script
    assert "intel_vpu" in script
    assert "ivpu" in script
    assert "DRIVER_VERSION" in script


@LINUX_ONLY
def test_linux_shell_scripts_parse_with_bash():
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash not available")

    scripts = [
        ROOT / "setup.sh",
        ROOT / "start_server.sh",
        *sorted((ROOT / "setup" / "linux").glob("*.sh")),
    ]
    for script in scripts:
        result = subprocess.run(
            [bash, "-n", str(script)],
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, f"{script}: {result.stderr}"
