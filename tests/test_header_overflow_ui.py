from app.config import Settings  # noqa: F401 - installs composed UI extensions
from app.header_overflow_ui import HEADER_OVERFLOW_JS
from app.ui_extension import inject_multimodal_ui


def test_header_overflow_extension_is_injected_once_after_diagnostics():
    html = "<html><head></head><body></body></html>"

    rendered = inject_multimodal_ui(html)
    rendered_twice = inject_multimodal_ui(rendered)

    assert rendered.count('id="ovllm-header-overflow-extension"') == 1
    assert rendered.count('id="ovllm-header-overflow-extension-styles"') == 1
    assert rendered_twice.count('id="ovllm-header-overflow-extension"') == 1
    assert rendered.index('id="ovllm-system-doctor-extension"') < rendered.index(
        'id="ovllm-header-overflow-extension"'
    )
    assert rendered.index('id="ovllm-header-overflow-extension"') < rendered.index(
        'id="ovllm-model-progress-extension"'
    )


def test_header_overflow_keeps_primary_controls_visible_and_restores_desktop_order():
    assert "add-model-btn" in HEADER_OVERFLOW_JS
    assert "export-chat-btn" in HEADER_OVERFLOW_JS
    assert "theme-toggle-btn" in HEADER_OVERFLOW_JS
    assert "advisor-open-btn" in HEADER_OVERFLOW_JS
    assert "doctor-btn" in HEADER_OVERFLOW_JS
    assert "settings-toggle-btn" in HEADER_OVERFLOW_JS
    assert "marker.parentNode?.insertBefore(button, marker.nextSibling)" in HEADER_OVERFLOW_JS
    assert "button.setAttribute('role', 'menuitem')" in HEADER_OVERFLOW_JS
    assert "button.removeAttribute('role')" in HEADER_OVERFLOW_JS
    assert "button.tabIndex = -1" in HEADER_OVERFLOW_JS
    assert "button.removeAttribute('tabindex')" in HEADER_OVERFLOW_JS
    assert "item.setAttribute('role', 'none')" in HEADER_OVERFLOW_JS


def test_header_overflow_has_keyboard_and_outside_click_dismissal():
    assert "aria-haspopup" in HEADER_OVERFLOW_JS
    assert "aria-expanded" in HEADER_OVERFLOW_JS
    assert "menu.setAttribute('aria-label', 'More actions')" in HEADER_OVERFLOW_JS
    assert "event.key === 'Escape'" in HEADER_OVERFLOW_JS
    assert "document.addEventListener('pointerdown'" in HEADER_OVERFLOW_JS
    assert "closeMenu({ restoreFocus: true })" in HEADER_OVERFLOW_JS
    assert "ArrowDown: 'next'" in HEADER_OVERFLOW_JS
    assert "ArrowUp: 'previous'" in HEADER_OVERFLOW_JS
    assert "Home: 'first'" in HEADER_OVERFLOW_JS
    assert "End: 'last'" in HEADER_OVERFLOW_JS
    assert "openMenu({ focus: event.key === 'ArrowUp' ? 'last' : 'first' })" in HEADER_OVERFLOW_JS


def test_header_overflow_closes_actions_without_leaving_hidden_focus():
    assert "function closeAfterAction()" in HEADER_OVERFLOW_JS
    assert "queueMicrotask" in HEADER_OVERFLOW_JS
    assert "const focusStayedInMenu = menu.contains(document.activeElement)" in HEADER_OVERFLOW_JS
    assert "closeMenu({ restoreFocus: focusStayedInMenu })" in HEADER_OVERFLOW_JS
    assert "if (event.key === 'Tab')" in HEADER_OVERFLOW_JS
    assert "if (event.shiftKey) trigger.focus()" in HEADER_OVERFLOW_JS
    assert "else (settingsButton || trigger).focus()" in HEADER_OVERFLOW_JS
    assert "trigger.setAttribute('aria-label', 'Close more actions')" in HEADER_OVERFLOW_JS
    assert "trigger.setAttribute('aria-label', 'Open more actions')" in HEADER_OVERFLOW_JS
