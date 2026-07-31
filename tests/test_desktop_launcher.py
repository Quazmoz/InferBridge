import os
import socket
from types import SimpleNamespace

from app import desktop_launcher, desktop_server
from app.desktop_launcher import InstanceLock


def test_port_selection_falls_back_when_preferred_is_busy():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        selected = desktop_launcher.choose_available_port(port)
    assert selected != port
    assert 1 <= selected <= 65535


def test_instance_verification_requires_matching_nonce(monkeypatch):
    metadata = desktop_launcher.InstanceMetadata(1, 8123, "expected", "app.exe", "now")
    monkeypatch.setattr(
        desktop_launcher,
        "_http_json",
        lambda url, timeout=1.5: (
            {"instance_nonce": "other"} if url.endswith("/desktop/instance") else {"status": "ok"}
        ),
    )
    assert desktop_launcher.verify_instance(metadata) is False


def test_stale_metadata_is_rejected(monkeypatch):
    metadata = desktop_launcher.InstanceMetadata(999999, 8123, "expected", "app.exe", "now")
    monkeypatch.setattr(desktop_launcher, "_http_json", lambda *args, **kwargs: None)
    assert desktop_launcher.verify_instance(metadata) is False


def test_second_lock_acquire_returns_false_without_raising(tmp_path):
    """A second launch while the first holds the lock must fail cleanly.

    On Windows the msvcrt byte-range lock held by the first instance makes the
    lock file's first byte unreadable from a second handle. Acquiring the lock
    a second time must report contention by returning False, not crash the
    launcher with an unhandled PermissionError.
    """
    lock_path = tmp_path / "launcher.lock"
    first = InstanceLock(lock_path)
    second = InstanceLock(lock_path)
    assert first.acquire() is True
    try:
        assert second.acquire() is False
    finally:
        second.release()
        first.release()
    # Once the first instance releases, a fresh acquire must succeed again.
    third = InstanceLock(lock_path)
    assert third.acquire() is True
    third.release()


def test_prepare_desktop_environment_clears_stale_launch_flags(monkeypatch, tmp_path):
    monkeypatch.setenv("OV_LLM_PORTABLE", "1")
    monkeypatch.setenv("OV_LLM_DATA_DIR", "stale")
    monkeypatch.setenv("OV_LLM_MOCK", "1")

    desktop_server.prepare_desktop_environment()

    assert os.environ["OV_LLM_DESKTOP"] == "1"
    assert "OV_LLM_PORTABLE" not in os.environ
    assert "OV_LLM_DATA_DIR" not in os.environ
    assert "OV_LLM_MOCK" not in os.environ

    desktop_server.prepare_desktop_environment(
        portable=True,
        data_dir=str(tmp_path),
        mock=True,
    )
    assert os.environ["OV_LLM_PORTABLE"] == "1"
    assert os.environ["OV_LLM_DATA_DIR"] == str(tmp_path)
    assert os.environ["OV_LLM_MOCK"] == "1"


def test_server_child_consumes_control_token_environment(monkeypatch):
    captured = {}

    def fake_run_server(**kwargs):
        captured.update(kwargs)
        return 7

    monkeypatch.setattr(desktop_server, "run_server", fake_run_server)
    monkeypatch.setenv("OV_LLM_DESKTOP_CONTROL_TOKEN", "environment-secret")
    args = SimpleNamespace(
        port=8123,
        instance_nonce="nonce",
        control_token="",
        owner_pid=123,
        owner_created_at=456.0,
        portable=False,
        data_dir=None,
        mock=True,
    )

    assert desktop_launcher._server_child(args) == 7
    assert captured["control_token"] == "environment-secret"
    assert "OV_LLM_DESKTOP_CONTROL_TOKEN" not in os.environ


def test_explicit_control_token_wins_but_environment_is_still_removed(monkeypatch):
    captured = {}

    def fake_run_server(**kwargs):
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(desktop_server, "run_server", fake_run_server)
    monkeypatch.setenv("OV_LLM_DESKTOP_CONTROL_TOKEN", "stale-secret")
    args = SimpleNamespace(
        port=8123,
        instance_nonce="nonce",
        control_token="explicit-secret",
        owner_pid=0,
        owner_created_at=0.0,
        portable=False,
        data_dir=None,
        mock=False,
    )

    assert desktop_launcher._server_child(args) == 0
    assert captured["control_token"] == "explicit-secret"
    assert "OV_LLM_DESKTOP_CONTROL_TOKEN" not in os.environ
