"""Regression coverage for the Microsoft Store native-window entrypoint."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.desktop_controller import DesktopServerController
from app.desktop_launcher import InstanceMetadata, main
from app.store_window import _validate_port, open_store_window, run_store_window


@pytest.mark.parametrize("port", [0, -1, 65536, True, 8123.0, "8123"])
def test_store_window_rejects_invalid_ports(port):
    with pytest.raises(ValueError, match="between 1 and 65535"):
        _validate_port(port)


def test_store_window_refuses_nonpackaged_execution():
    assert not open_store_window(8123, "nonce")
    with pytest.raises(RuntimeError, match="installed MSIX"):
        run_store_window(8123, "nonce")


def test_packaged_chat_uses_window_instead_of_external_browser(monkeypatch):
    controller = object.__new__(DesktopServerController)
    controller.metadata = InstanceMetadata(
        pid=123, port=8123, nonce="known-session", executable="InferBridge.exe", started_at=""
    )
    opened = Mock(return_value=True)
    monkeypatch.setattr("app.desktop_controller.is_packaged", lambda: True)
    monkeypatch.setattr("app.desktop_controller.open_store_window", opened)
    monkeypatch.setattr(
        "app.desktop_controller.open_browser",
        lambda url: pytest.fail(f"Store app opened external browser: {url}"),
    )
    assert controller.open_chat() is True
    opened.assert_called_once_with(8123, "known-session")


def test_window_child_dispatch_does_not_start_tray(monkeypatch):
    invoked = Mock(return_value=0)
    monkeypatch.setattr("app.store_window.run_store_window", invoked)
    assert main(["--window-child", "--port", "8123", "--instance-nonce", "known-session"]) == 0
    invoked.assert_called_once_with(8123, "known-session")


def test_window_child_requires_explicit_port():
    with pytest.raises(SystemExit):
        main(["--window-child", "--instance-nonce", "known-session"])


def test_native_store_window_hosts_loopback_ui_with_edgechromium(tmp_path, monkeypatch):
    monkeypatch.setattr("app.store_window._store_runtime_available", lambda: True)
    validated = Mock(return_value=True)
    monkeypatch.setattr("app.store_window.verify_instance", validated)
    monkeypatch.setattr(
        "app.store_window.resolve_runtime_paths",
        lambda **kwargs: SimpleNamespace(data_root=tmp_path),
    )
    opened = Mock()
    started = Mock()

    class ClosedEvent:
        def __iadd__(self, callback):
            return self

    window = SimpleNamespace(events=SimpleNamespace(closed=ClosedEvent()))
    opened.return_value = window
    monkeypatch.setitem(
        sys.modules,
        "webview",
        SimpleNamespace(create_window=opened, start=started),
    )
    assert run_store_window(8123, "known-session") == 0
    assert validated.call_args.args[0].nonce == "known-session"
    assert opened.call_args.args == ("InferBridge", "http://127.0.0.1:8123/")
    assert callable(started.call_args.args[0])
    assert started.call_args.kwargs["gui"] == "edgechromium"
    assert started.call_args.kwargs["private_mode"] is False
    assert started.call_args.kwargs["storage_path"] == str(tmp_path / "webview")


def test_packaged_window_prefers_bundled_native_client(tmp_path, monkeypatch):
    executable = tmp_path / "InferBridge.exe"
    executable.write_bytes(b"MZ")
    monkeypatch.setattr("app.store_window._store_runtime_available", lambda: True)
    monkeypatch.setattr("app.store_window.sys.executable", str(executable))
    started = Mock()
    monkeypatch.setattr("app.store_window.subprocess.Popen", started)

    # Without the native payload the WebView2 child remains the fallback.
    assert open_store_window(8123, "known-session") is True
    assert started.call_args.args[0][:2] == [str(executable), "--window-child"]

    native = tmp_path / "native" / "InferBridge.Native.exe"
    native.parent.mkdir()
    native.write_bytes(b"MZ")
    assert open_store_window(8123, "known-session") is True
    # The native client reads verified session metadata itself: no URL, port or nonce.
    assert started.call_args.args[0] == [str(native.resolve())]
    assert started.call_args.kwargs["cwd"] == str(native.parent.resolve())
