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


def test_every_managed_header_action_is_reachable_by_keyboard():
    """Arrow navigation collects items by the `icon-btn` class.

    A header button contributed by another extension is announced as a menu item once it
    moves into the menu, so it must carry that class too or keyboard users step straight
    past it while a screen reader still reads it out.
    """

    from app.advisor_ui import SCRIPT_1

    assert "'.ov-header-overflow-item .icon-btn'" in HEADER_OVERFLOW_JS
    assert "button.className = 'icon-btn'" in SCRIPT_1
    assert "button.id = 'advisor-open-btn'" in SCRIPT_1


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
    assert "const active = document.activeElement" in HEADER_OVERFLOW_JS
    assert "closeMenu({ restoreFocus: !focusMovedAway })" in HEADER_OVERFLOW_JS
    assert "if (event.key === 'Tab')" in HEADER_OVERFLOW_JS
    assert "if (event.shiftKey) trigger.focus()" in HEADER_OVERFLOW_JS
    assert "else (settingsButton || trigger).focus()" in HEADER_OVERFLOW_JS
    assert "trigger.setAttribute('aria-label', 'Close more actions')" in HEADER_OVERFLOW_JS
    assert "trigger.setAttribute('aria-label', 'Open more actions')" in HEADER_OVERFLOW_JS


def test_activating_an_item_by_its_label_returns_focus_to_the_trigger():
    """Clicking the label forwards a programmatic click, which never moves focus.

    Treating the resulting body-level focus as "nothing took focus" is what keeps the
    menu trigger focused instead of dropping keyboard users at the top of the document.
    """

    assert "active !== document.body" in HEADER_OVERFLOW_JS
    assert "active !== document.documentElement" in HEADER_OVERFLOW_JS
    assert "!menu.contains(active)" in HEADER_OVERFLOW_JS
    # An action that focuses its own target — a modal input, say — still keeps it.
    assert "const focusMovedAway = Boolean(active)" in HEADER_OVERFLOW_JS
    assert "button.click();" in HEADER_OVERFLOW_JS
