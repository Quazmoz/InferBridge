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


def _metadata_payload(**changes):
    payload = {
        "pid": 1234,
        "port": 8123,
        "nonce": "nonce-value",
        "executable": "InferBridge.exe",
        "started_at": "2026-08-19T06:59:00Z",
    }
    payload.update(changes)
    return payload


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("pid", True),
        ("pid", 1234.5),
        ("pid", "1234"),
        ("pid", float("inf")),
        ("port", False),
        ("port", 8123.5),
        ("port", "8123"),
        ("port", float("nan")),
        ("nonce", {"unexpected": "object"}),
        ("executable", ["InferBridge.exe"]),
        ("started_at", 123),
    ],
)
def test_instance_metadata_rejects_lossy_or_structured_values(field, value):
    assert desktop_launcher.InstanceMetadata.from_json(_metadata_payload(**{field: value})) is None


def test_nonfinite_instance_metadata_file_is_treated_as_stale(tmp_path):
    path = tmp_path / "desktop-instance.json"
    path.write_text(
        '{"pid":Infinity,"port":8123,"nonce":"n","executable":"InferBridge.exe",'
        '"started_at":"now"}',
        encoding="utf-8",
    )

    assert desktop_launcher._read_metadata(path) is None
