from app.config import Settings  # noqa: F401 - installs composed UI extensions
from app.context_budget_ui import CONTEXT_BUDGET_JS
from app.ui_extension import inject_multimodal_ui


def test_context_budget_ui_is_injected_once_after_chat_context() -> None:
    html = '<html><body><div id="input-area"><div class="footer-right"><span id="token-counter"></span></div></div></body></html>'

    rendered = inject_multimodal_ui(html)
    rendered_twice = inject_multimodal_ui(rendered)

    context_marker = 'id="ovllm-chat-context-extension"'
    budget_marker = 'id="ovllm-context-budget-extension"'
    assert rendered.count(budget_marker) == 1
    assert rendered_twice.count(budget_marker) == 1
    assert rendered.index(context_marker) < rendered.index(budget_marker)


def test_context_budget_ui_uses_exact_preflight_payload() -> None:
    script = CONTEXT_BUDGET_JS

    assert "const ENDPOINT = '/v1/chat/context-budget'" in script
    assert "messages.push(...apiMessages(history))" in script
    assert "max_tokens:" in script
    assert "image_count: pendingImageCount()" in script
    assert "method: 'POST'" in script
    assert "cache: 'no-store'" in script
    assert "inspectController?.abort()" in script


def test_context_budget_ui_surfaces_omissions_and_output_limits() -> None:
    script = CONTEXT_BUDGET_JS

    assert "Context budget" in script
    assert "Omitted message preview" in script
    assert "older turn" in script
    assert "Available output" in script
    assert "Reduce output to fit" in script
    assert "Start new chat from here" in script
    assert "Leading system instructions remain pinned" in script
    assert "attachment_token_estimate" in script


def test_context_budget_ui_is_accessible_and_uses_safe_dom_rendering() -> None:
    script = CONTEXT_BUDGET_JS

    assert "chip.setAttribute('aria-expanded', 'false')" in script
    assert "panel.setAttribute('role', 'dialog')" in script
    assert "panel.setAttribute('aria-labelledby', 'ovcb-title')" in script
    assert "closeButton.setAttribute('aria-label', 'Close context budget')" in script
    assert "event.key === 'Escape'" in script
    assert ".textContent" in script
    assert "innerHTML" not in script


def test_context_budget_ui_observes_only_attachment_tray_mutations() -> None:
    script = CONTEXT_BUDGET_JS

    assert "trayObserver.observe(tray, { childList: true })" in script
    assert "attachmentObserver.observe(inputArea" not in script
    assert "traySearchObserver.observe(document.documentElement" in script
