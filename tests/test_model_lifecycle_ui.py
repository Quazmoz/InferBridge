from app.model_lifecycle_ui import MODEL_LIFECYCLE_EXTENSION_JS
from app.ui_extension import inject_multimodal_ui


def test_model_lifecycle_extension_is_injected_once_before_release_extension():
    html = "<html><body><main>chat</main></body></html>"

    injected = inject_multimodal_ui(html)

    assert injected.count('id="ovllm-model-lifecycle-extension"') == 1
    assert injected.index('id="ovllm-model-lifecycle-extension"') < injected.index(
        'id="ovllm-release-extension"'
    )
    assert inject_multimodal_ui(injected) == injected


def test_model_options_have_color_and_text_status_fallbacks():
    script = MODEL_LIFECYCLE_EXTENSION_JS

    assert "ovllm-model-state-loaded" in script
    assert "ovllm-model-state-ready" in script
    assert "ovllm-model-state-working" in script
    assert "ovllm-model-state-unavailable" in script
    assert "ovllm-model-state-cancelled" in script
    assert "ovllm-model-state-error" in script
    assert "option.style.color" in script
    assert "option.style.backgroundColor" in script
    assert "● Loaded" in script
    assert "● Converted" in script
    assert "● Not converted" in script
    assert "MutationObserver" in script
    assert "forced-colors:active" in script


def test_loading_or_converting_a_second_model_requires_an_explicit_choice():
    script = MODEL_LIFECYCLE_EXTENSION_JS

    assert "requestModelLoad = function" in script
    assert "requestModelConvert = function" in script
    assert "loadedPeersFor" in script
    assert "Keep loaded and continue" in script
    assert "Unload others and continue" in script
    assert "High-memory systems may handle this" in script
    assert "reduce generation speed" in script
    assert "guardBypass" in script


def test_recommended_action_unloads_other_models_through_authenticated_api():
    script = MODEL_LIFECYCLE_EXTENSION_JS

    assert "'/v1/models/unload'" in script
    assert "authHeaders({ 'Content-Type': 'application/json' })" in script
    assert "await updateStatus()" in script
    assert "Could not unload" in script
    assert "modalError" in script


def test_multi_model_warning_is_accessible_and_keyboard_dismissible():
    script = MODEL_LIFECYCLE_EXTENSION_JS

    assert "aria-live" in script
    assert "aria-modal" in script
    assert "aria-labelledby" in script
    assert "event.key === 'Escape'" in script
    assert "lastFocusedElement.focus()" in script
    assert "aria-describedby" in script
