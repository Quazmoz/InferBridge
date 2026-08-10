"""Source-level contract checks for the Local Connection Hub."""

import sys

from app import connection_hub_ui, ui_extension


def test_connection_hub_ui_exposes_operational_connection_workflow_without_server_secrets():
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
        "browserApiKey",
        "Authorization",
        "navigator.clipboard",
        "document.execCommand",
        "aria-live",
        "aria-busy",
        "Escape",
    ):
        assert token in javascript

    # The Hub may use the API credential the user already entered into this browser to
    # authorize its protected self-test coordinator. It still only displays/copies the
    # generic placeholder and never receives the configured server-side secret.
    assert "localStorage.getItem('ovllm.apikey.v1')" in javascript
    assert "path === TEST_PATH" in javascript
    assert "api_key_placeholder" in javascript
    assert "configured server secret" in javascript
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
    assert "Close the Hub, enter the InferBridge API key" in javascript


def test_connection_hub_ui_is_responsive_accessible_and_not_modal_locked_while_testing():
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
    assert "function setRunning(value)" in javascript
    assert "if (running) return;\n    modal.classList.add" not in javascript


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
