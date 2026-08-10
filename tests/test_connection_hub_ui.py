"""Source-level contract checks for the Local Connection Hub."""

import sys

from app import connection_hub_ui, ui_extension


def test_connection_hub_ui_exposes_operational_connection_workflow_without_secrets():
    javascript = connection_hub_ui.CONNECTION_HUB_JS

    for token in (
        "Local Connection Hub",
        "/internal/connection-hub",
        "/internal/connection-hub/self-test",
        "Base URL",
        "OpenAI Python SDK",
        "curl.exe",
        "Run connection self-test",
        "Advanced LAN access",
        "YOUR_INFERBRIDGE_API_KEY",
        "not-required",
        "X-OV-LLM-UI",
        "navigator.clipboard",
        "document.execCommand",
        "aria-live",
        "Escape",
    ):
        assert token in javascript

    assert "localStorage.getItem('ovllm.apikey.v1')" not in javascript
    assert 'localStorage.getItem("ovllm.apikey.v1")' not in javascript
    assert "ngrok" not in javascript.lower()
    assert "cloudflare" not in javascript.lower()
    assert "tailscale" not in javascript.lower()


def test_connection_hub_ui_has_explicit_model_selection_and_independent_test_statuses():
    javascript = connection_hub_ui.CONNECTION_HUB_JS

    assert "Multiple generation-capable models are loaded" in javascript
    assert "Select a loaded model..." in javascript
    assert "Only loaded generation-capable models" in javascript
    for test_id in (
        "models",
        "non_streaming",
        "streaming",
        "cancellation",
        "authentication",
    ):
        assert f"['{test_id}'" in javascript
    for status in ("Not run", "Running", "Passed", "Failed", "Skipped"):
        assert status in javascript
    assert "Review each check independently" in javascript


def test_connection_hub_ui_is_responsive_and_accessible():
    css = connection_hub_ui.CONNECTION_HUB_CSS
    javascript = connection_hub_ui.CONNECTION_HUB_JS

    assert "@media(max-width:720px)" in css
    assert "focus-visible" in css
    assert 'role="dialog"' in javascript
    assert 'aria-modal="true"' in javascript
    assert "returnFocus" in javascript
    assert "focusables()" in javascript
    assert "document.getElementById('app')?.setAttribute('inert','')" in javascript
    assert "document.getElementById('app')?.removeAttribute('inert')" in javascript


def test_connection_hub_ui_composes_once_into_the_static_surface(monkeypatch):
    monkeypatch.setattr(ui_extension, "inject_multimodal_ui", lambda html: html)
    monkeypatch.delattr(
        ui_extension, "_CONNECTION_HUB_UI_EXTENSION_INSTALLED", raising=False
    )
    server = sys.modules.get("app.server")
    if server is not None:
        monkeypatch.setattr(server, "inject_multimodal_ui", server.inject_multimodal_ui)

    connection_hub_ui.install_connection_hub_ui_extension()
    page = ui_extension.inject_multimodal_ui("<html><body></body></html>")

    assert page.count('id="ovllm-connection-hub-extension"') == 1
    assert page.count('id="ovllm-connection-hub-extension-styles"') == 1
    assert "CONNECTION_HUB_JS" not in page
