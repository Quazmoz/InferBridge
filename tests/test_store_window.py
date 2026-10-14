"""Regression coverage for the Microsoft Store native-window entrypoint."""

from __future__ import annotations

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
    assert (
        main(["--window-child", "--port", "8123", "--instance-nonce", "known-session"])
        == 0
    )
    invoked.assert_called_once_with(8123, "known-session")


def test_window_child_requires_explicit_port():
    with pytest.raises(SystemExit):
        main(["--window-child", "--instance-nonce", "known-session"])
