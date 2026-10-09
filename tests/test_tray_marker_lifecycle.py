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



class _FailingStartupStub(_StartupStub):
    def __init__(self, tmp_path) -> None:
        super().__init__(tmp_path)
        self.args = SimpleNamespace(
            start_stopped=False,
            headless=True,
            no_browser=True,
            startup=False,
        )

    def _start_server(self, *, open_chat: bool) -> None:
        assert open_chat is False
        raise RuntimeError("Configured port 8123 is unavailable")

    def _run_headless(self) -> int:
        raise AssertionError("Failed headless startup must not enter the polling loop")


def test_headless_startup_failure_is_nonzero_and_releases_owner_lock(tmp_path):
    from app.tray_state import TrayPhase

    stub = _FailingStartupStub(tmp_path)
    assert stub.run() == 6
    assert stub.snapshot.phase is TrayPhase.ERROR
    assert "Configured port 8123 is unavailable" in stub.snapshot.warning
    assert stub.cleaned is True
    assert stub.lock.released is True


def test_interactive_startup_failure_keeps_tray_available(monkeypatch, tmp_path):
    from app import tray_runtime
    from app.tray_state import TrayPhase

    stub = _FailingStartupStub(tmp_path)
    stub.args.headless = False
    tray_entered = []
    dialogs = []
    stub._run_tray = lambda: tray_entered.append(True) or 0
    monkeypatch.setattr(tray_runtime, "show_dialog", lambda *args, **kw: dialogs.append((args, kw)))

    assert stub.run() == 0
    assert tray_entered == [True]
    assert len(dialogs) == 1 and dialogs[0][1].get("error") is True
    assert stub.snapshot.phase is TrayPhase.ERROR
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
