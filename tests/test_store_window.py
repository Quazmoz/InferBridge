"""Windows native entrypoint is shared by EXE, portable and Store packages."""

from __future__ import annotations

import sys
from unittest.mock import Mock

import pytest

from app.desktop_controller import DesktopServerController
from app.desktop_launcher import InstanceMetadata, main
from app.store_window import _validate_port, open_store_window


@pytest.mark.parametrize("port", [0, -1, 65536, True, 8123.0, "8123"])
def test_native_window_rejects_invalid_ports(port):
    with pytest.raises(ValueError, match="between 1 and 65535"):
        _validate_port(port)


def test_native_window_refuses_non_windows_execution():
    assert not open_store_window(8123, "nonce")


@pytest.mark.parametrize("nonce", ["", "x" * 513, None, 1234])
def test_native_window_rejects_invalid_session_identity(nonce, monkeypatch):
    monkeypatch.setattr("app.store_window._store_runtime_available", lambda: True)
    monkeypatch.setattr("app.store_window.subprocess.Popen", Mock(side_effect=AssertionError))
    assert open_store_window(8123, nonce) is False


def test_windows_chat_opens_native_even_when_not_msix(monkeypatch):
    controller = object.__new__(DesktopServerController)
    controller.metadata = InstanceMetadata(
        pid=123, port=8123, nonce="known-session", executable="InferBridge.exe", started_at=""
    )
    opened = Mock(return_value=True)
    monkeypatch.setattr("app.desktop_controller.is_packaged", lambda: False)
    monkeypatch.setattr("app.desktop_controller.sys.platform", "win32")
    monkeypatch.setattr("app.desktop_controller.open_store_window", opened)
    monkeypatch.setattr(
        "app.desktop_controller.open_browser",
        lambda url: pytest.fail(f"Windows app opened external browser: {url}"),
    )
    assert controller.open_chat() is True
    opened.assert_called_once_with(8123, "known-session")


def test_webview_child_launcher_has_been_removed():
    with pytest.raises(SystemExit):
        main(["--window-child", "--port", "8123", "--instance-nonce", "nonce"])


def test_windows_native_window_fails_closed_when_payload_missing(tmp_path, monkeypatch):
    executable = tmp_path / "InferBridge.exe"
    executable.write_bytes(b"MZ")
    monkeypatch.setattr("app.store_window._store_runtime_available", lambda: True)
    monkeypatch.setattr("app.store_window.sys.executable", str(executable))
    process = Mock()
    monkeypatch.setattr("app.store_window.subprocess.Popen", process)
    assert open_store_window(8123, "known-session") is False
    process.assert_not_called()

    native = tmp_path / "native" / "InferBridge.Native.exe"
    native.parent.mkdir()
    native.write_bytes(b"MZ")
    assert open_store_window(8123, "known-session") is True
    assert process.call_args.args[0] == [str(native.resolve())]
    assert process.call_args.kwargs["cwd"] == str(native.parent.resolve())


def test_native_window_never_imports_webview():
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1] / "app" / "store_window.py"
    ).read_text(encoding="utf-8")
    assert "import webview" not in source
    assert "--window-child" not in source
    assert "http://127.0.0.1:" not in source


def test_tray_native_is_default_and_browser_is_explicit_secondary_action():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    menu = (root / "app" / "tray_menu.py").read_text(encoding="utf-8")
    runtime = (root / "app" / "tray_runtime.py").read_text(encoding="utf-8")
    controller = (root / "app" / "desktop_controller.py").read_text(encoding="utf-8")

    assert '"Open InferBridge"' in menu
    assert '"Open web GUI"' in menu
    assert '"Open web GUI",\n                self._action(self.open_web_ui)' in menu
    assert "def open_web_ui(self)" in menu
    assert "open_store_window(metadata.port, metadata.nonce)" in runtime
    assert "def open_web_ui(self)" in controller
    assert "sys.platform == \"win32\" or is_packaged()" in controller
