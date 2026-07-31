from types import SimpleNamespace

import pytest

from app import desktop_controller, desktop_launcher
from app.desktop_controller import DesktopServerController, ServerControllerOptions


def test_wait_for_readiness_stops_before_polling_after_child_exit(monkeypatch) -> None:
    metadata = desktop_launcher.InstanceMetadata(
        pid=123,
        port=8123,
        nonce="nonce",
        executable="InferBridge.exe",
        started_at="now",
    )
    calls = 0

    def unexpected_http_call(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return None

    monkeypatch.setattr(desktop_launcher, "_http_json", unexpected_http_call)

    assert (
        desktop_launcher.wait_for_readiness(
            metadata,
            timeout=60.0,
            is_alive=lambda: False,
        )
        is False
    )
    assert calls == 0


def test_controller_surfaces_immediate_child_exit(monkeypatch, tmp_path) -> None:
    class ExitedChild:
        pid = 4321
        returncode = 17

        def poll(self):
            return self.returncode

        def terminate(self):
            raise AssertionError("An exited child must not be terminated")

    paths = SimpleNamespace(launcher_metadata_file=tmp_path / "instance.json")
    controller = DesktopServerController(
        paths=paths,
        options=ServerControllerOptions(
            preferred_port=8123,
            startup_timeout_seconds=60.0,
        ),
        log_path=tmp_path / "desktop.log",
    )
    child = ExitedChild()

    monkeypatch.setattr(controller, "recover_stale_metadata", lambda: None)
    monkeypatch.setattr(controller, "_spawn", lambda _metadata, _token: child)
    monkeypatch.setattr(desktop_controller, "choose_available_port", lambda _preferred: 8123)

    with pytest.raises(RuntimeError, match="exited during startup with code 17"):
        controller.start()

    assert controller.last_exit_code == 17
    assert controller.last_error == (
        "The local server exited during startup with code 17. "
        "Review the sanitized tray and desktop logs."
    )
    assert controller.child is None
    assert controller.metadata is None
