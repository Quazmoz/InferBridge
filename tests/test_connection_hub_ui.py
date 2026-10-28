"""Source-level contract checks for the Local Connection Hub."""

from app import connection_hub_ui, ui_registry


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


def test_connection_hub_ui_composes_once_into_the_static_surface():
    connection_hub_ui.install_connection_hub_ui_extension()
    page = ui_registry.render_only(
        "<html><body></body></html>", ("ovllm-connection-hub-extension",)
    )

    assert page.count('id="ovllm-connection-hub-extension"') == 1
    assert page.count('id="ovllm-connection-hub-extension-styles"') == 1
    assert "CONNECTION_HUB_JS" not in page


def _run_tool_setup(script: str) -> dict:
    """Execute the pure tool-setup builders in Node with a minimal window."""
    import json
    import shutil
    import subprocess

    import pytest

    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is not installed")
    program = (
        "const window = {};\n"
        + connection_hub_ui.TOOL_SETUP_JS
        + "\nconst setup = window.__ovllmToolSetup;\n"
        + script
    )
    result = subprocess.run(
        [node, "-e", program], capture_output=True, text=True, timeout=30, check=True
    )
    return json.loads(result.stdout)


_METADATA = """
const meta = (required, context) => ({
  base_url: 'http://127.0.0.1:19437/v1',
  authentication: {required},
  models: [{id: 'qwen-coder', loaded: true, generation_capable: true,
            context_tokens: context, max_output_tokens: context ? 4096 : null},
           {id: 'embed', loaded: true, generation_capable: false}],
});
"""


def test_tool_setup_values_come_from_engine_metadata_with_placeholders_only():
    out = _run_tool_setup(
        _METADATA
        + """
const open = setup.values(meta(false, 32768), 'qwen-coder');
const secured = setup.values(meta(true, null), 'qwen-coder');
console.log(JSON.stringify({
  open, secured,
  none: setup.values(meta(false, 32768), 'embed'),
  ids: setup.clients.map(c => c.id),
  native: setup.clients.filter(c => c.native).map(c => c.id),
  fields: setup.clients.map(c => setup.fields(c.id, open)),
  steps: setup.clients.map(c => setup.steps(c.id, open).length),
}));
"""
    )
    assert out["open"] == {
        "base": "http://127.0.0.1:19437/v1",
        "model": "qwen-coder",
        "auth": False,
        "key": "not-required",
        "context": 32768,
        "output": 4096,
        "input": 28672,
    }
    assert out["secured"]["key"] == "YOUR_INFERBRIDGE_API_KEY"
    assert out["secured"]["context"] is None
    assert out["none"] is None  # Embedding models are never offered to coding tools.
    assert out["ids"] == [
        "continue",
        "copilot-cli",
        "vscode-copilot",
        "copilot-app",
        "cline",
        "roo",
        "generic",
    ]
    assert out["native"] == ["continue", "copilot-cli"]
    for rows in out["fields"]:
        assert ["Base URL", "http://127.0.0.1:19437/v1"] in rows
        assert ["Model ID", "qwen-coder"] in rows
        assert ["API key", "not-required"] in rows
    assert all(count >= 3 for count in out["steps"])


def test_tool_setup_snippets_match_documented_client_formats():
    import json

    out = _run_tool_setup(
        _METADATA
        + """
const open = setup.values(meta(false, 32768), 'qwen-coder');
const secured = setup.values(meta(true, null), 'qwen-coder');
const quote = setup.values({...meta(false, null), models: [{id: "o'brien", loaded: true,
  generation_capable: true}]}, "o'brien");
console.log(JSON.stringify({
  cont: setup.snippets('continue', secured)[0][1],
  contOpen: setup.snippets('continue', open)[0][1],
  cli: setup.snippets('copilot-cli', secured)[0][1],
  cliQuote: setup.snippets('copilot-cli', quote)[0][1],
  env: setup.copilotEnv(open),
  vscode: setup.snippets('vscode-copilot', open),
  vscodeSecured: setup.snippets('vscode-copilot', secured)[0][1],
  notes: [setup.agenticNote(null), setup.agenticNote(8192), setup.agenticNote(65536),
          setup.agenticNote(200000)],
}));
"""
    )
    assert 'apiBase: "http://127.0.0.1:19437/v1"' in out["cont"]
    assert 'apiKey: "YOUR_INFERBRIDGE_API_KEY"' in out["cont"]
    assert "provider: openai" in out["cont"] and "contextLength" not in out["cont"]
    assert "contextLength: 32768" in out["contOpen"]
    assert "'COPILOT_PROVIDER_BASE_URL', 'http://127.0.0.1:19437/v1', 'User'" in out["cli"]
    assert "SetEnvironmentVariable('COPILOT_PROVIDER_API_KEY'" not in out["cli"]
    assert "$env:COPILOT_PROVIDER_API_KEY" in out["cli"]
    assert "'o''brien'" in out["cliQuote"]
    assert ["COPILOT_PROVIDER_MAX_PROMPT_TOKENS", "28672"] in out["env"]
    custom = json.loads(out["vscode"][0][1])[0]
    assert custom["vendor"] == "customendpoint"
    assert custom["models"][0]["url"] == "http://127.0.0.1:19437/v1/chat/completions"
    assert custom["models"][0]["maxInputTokens"] == 28672
    assert json.loads(out["vscodeSecured"])[0]["apiKey"] == "${input:inferbridgeApiKey}"
    assert out["vscode"][1][0].startswith("Deprecated")
    assert '"github.copilot.chat.customOAIModels"' in out["vscode"][1][1]
    assert [note["warning"] for note in out["notes"]] == [True, True, True, False]
    assert "128k" in out["notes"][2]["text"]


def test_tool_setup_cards_are_accessible_and_reuse_the_self_test():
    javascript = connection_hub_ui.CONNECTION_HUB_JS
    assert "Connect a tool" in javascript
    assert 'aria-labelledby="ch-tools-heading"' in javascript
    assert "card.setAttribute('aria-labelledby', headingId)" in javascript
    assert "Test connection" in javascript
    assert (
        "if (event.target.closest('[data-tool-test]')) { void runSelfTest(); return; }"
        in javascript
    )
    assert "rel = 'noopener noreferrer'" in javascript
    # The browser never writes files or variables; one-click lives in the native app.
    assert "One-click in the Windows app" in javascript
    assert connection_hub_ui.EXTENSION.javascript.startswith(connection_hub_ui.TOOL_SETUP_JS)
