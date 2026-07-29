from app.config import Settings  # noqa: F401 - installs composed UI extensions
from app.gui_stability import GUI_STABILITY_CSS, GUI_STABILITY_JS
from app.ui_extension import inject_multimodal_ui


def test_gui_stability_extension_is_injected_once_and_last() -> None:
    html = "<html><head></head><body></body></html>"

    rendered = inject_multimodal_ui(html)
    rendered_twice = inject_multimodal_ui(rendered)

    assert rendered.count('id="ovllm-gui-stability-extension"') == 1
    assert rendered.count('id="ovllm-gui-stability-extension-styles"') == 1
    assert rendered_twice.count('id="ovllm-gui-stability-extension"') == 1
    assert rendered.index('id="ovllm-desktop-operations-extension"') < rendered.index(
        'id="ovllm-gui-stability-extension"'
    )


def test_reconnect_recomputes_legitimate_control_states() -> None:
    assert "wasUnavailable" in GUI_STABILITY_JS
    assert "repairControlsAfterReconnect" in GUI_STABILITY_JS
    assert "typeof updateModelUi === 'function'" in GUI_STABILITY_JS
    assert "typeof updateSendButtonState === 'function'" in GUI_STABILITY_JS
    assert "MutationObserver" in GUI_STABILITY_JS


def test_hugging_face_results_are_cancelled_bounded_and_rendered_as_text() -> None:
    assert "new AbortController()" in GUI_STABILITY_JS
    assert "items.slice(0, 100)" in GUI_STABILITY_JS
    assert "event.stopImmediatePropagation()" in GUI_STABILITY_JS
    assert "badge.textContent" in GUI_STABILITY_JS
    assert "name.textContent = modelId" in GUI_STABILITY_JS
    assert "meta.innerHTML" not in GUI_STABILITY_JS
    assert "Search returned an invalid response" in GUI_STABILITY_JS


def test_modal_reset_focus_and_overflow_keyboard_behavior_are_repaired() -> None:
    assert "customForm?.addEventListener('reset'" in GUI_STABILITY_JS
    assert "syncCustomFormUi" in GUI_STABILITY_JS
    assert "returnFocusToMore" in GUI_STABILITY_JS
    assert "moreTrigger?.focus()" in GUI_STABILITY_JS
    assert "ArrowDown" in GUI_STABILITY_JS
    assert "ArrowUp" in GUI_STABILITY_JS
    assert "moreWrap?.addEventListener('focusout'" in GUI_STABILITY_JS


def test_narrow_modal_and_search_layout_use_dynamic_viewport_units() -> None:
    assert "100dvh" in GUI_STABILITY_CSS
    assert "env(safe-area-inset-top)" in GUI_STABILITY_CSS
    assert "env(safe-area-inset-bottom)" in GUI_STABILITY_CSS
    assert "@media (max-width: 560px)" in GUI_STABILITY_CSS
    assert ".search-row" in GUI_STABILITY_CSS
    assert "--danger: var(--red)" in GUI_STABILITY_CSS


def test_activity_feed_and_toast_do_not_disrupt_user_context() -> None:
    assert "followLatest" in GUI_STABILITY_JS
    assert "priorTop" in GUI_STABILITY_JS
    assert "aria-live', 'polite'" in GUI_STABILITY_JS
    assert "aria-atomic', 'true'" in GUI_STABILITY_JS
