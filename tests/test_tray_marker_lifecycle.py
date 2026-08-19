from __future__ import annotations

import threading
from types import SimpleNamespace

from app.tray_app import TrayApplication
from app.tray_runtime import TrayRuntimeMixin


class _Lock:
    def __init__(self) -> None:
        self.released = False

    def acquire(self) -> bool:
        return True

    def release(self) -> None:
        self.released = True


class _StartupStub(TrayRuntimeMixin):
    def __init__(self, tmp_path) -> None:
        self.lock = _Lock()
        self.command_file = tmp_path / "tray-command.json"
        self.restart_request_file = tmp_path / "restart-server.request"
        self.args = SimpleNamespace(start_stopped=True, headless=True)
        self.cleaned = False

    def _run_headless(self) -> int:
        return 0

    def _shutdown_owned_resources(self) -> None:
        self.cleaned = True


def test_new_tray_owner_discards_stale_one_shot_markers(tmp_path):
    stub = _StartupStub(tmp_path)
    stub.command_file.write_text('{"command":"start"}', encoding="utf-8")
    stub.restart_request_file.write_text("restart\n", encoding="utf-8")

    assert stub.run() == 0

    assert not stub.command_file.exists()
    assert not stub.restart_request_file.exists()
    assert stub.cleaned is True
    assert stub.lock.released is True


def test_tray_shutdown_removes_all_session_markers(tmp_path):
    tray = object.__new__(TrayApplication)
    tray.stop_event = threading.Event()
    tray.heartbeat_file = tmp_path / "tray-heartbeat.json"
    tray.command_file = tmp_path / "tray-command.json"
    tray.restart_request_file = tmp_path / "restart-server.request"
    tray.poll_thread = None
    tray.controller = SimpleNamespace(stop=lambda: None)
    for marker in (tray.heartbeat_file, tray.command_file, tray.restart_request_file):
        marker.write_text("stale\n", encoding="utf-8")

    tray._shutdown_owned_resources()

    assert tray.stop_event.is_set()
    assert not tray.heartbeat_file.exists()
    assert not tray.command_file.exists()
    assert not tray.restart_request_file.exists()
