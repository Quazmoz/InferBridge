from app.cancellation_ui import CANCELLATION_UI_JS
from app.config import Settings  # noqa: F401 - installs composed UI extensions
from app.ui_extension import inject_multimodal_ui


def test_cancellation_ui_is_injected_once_before_progress_controller() -> None:
    html = '<html><body><div class="chat-column"><div id="chat-area"></div></div></body></html>'

    rendered = inject_multimodal_ui(html)
    rendered_twice = inject_multimodal_ui(rendered)

    cancellation = 'id="ovllm-model-cancellation-extension"'
    operation = 'id="ovllm-progress-operation-extension"'
    progress = 'id="ovllm-model-progress-extension"'
    assert rendered.count(cancellation) == 1
    assert rendered_twice.count(cancellation) == 1
    assert rendered.index(operation) < rendered.index(cancellation) < rendered.index(progress)


def test_cancellation_control_uses_exact_operation_identity() -> None:
    script = CANCELLATION_UI_JS

    assert "const CANCEL_PATH = '/v1/models/cancel'" in script
    assert "operation_id: operation.operationId" in script
    assert "model: operation.modelId" in script
    assert "model.can_cancel === true" in script
    assert "model.cancel_mode" in script
    assert "cancellationInFlight" in script
    assert "if (!operation.canCancel || cancellationInFlight) return" in script


def test_cancellation_control_is_guarded_and_accessible() -> None:
    script = CANCELLATION_UI_JS

    assert "window.confirm(confirmationMessage(operation))" in script
    assert "Cancel conversion" in script
    assert "Cancel preparation" in script
    assert "button.type = 'button'" in script
    assert "button.disabled = busy" in script
    assert "button.setAttribute('aria-label'" in script
    assert "item.setAttribute('role', 'status')" in script
    assert "item.setAttribute('aria-live', 'polite')" in script
    assert "prefers-reduced-motion" not in script


def test_cancellation_ui_handles_conflicts_without_unsafe_html() -> None:
    script = CANCELLATION_UI_JS

    assert "detail.message" in script
    assert "Cancellation failed with HTTP" in script
    assert "await refreshStatus()" in script
    assert "textContent" in script
    assert "innerHTML" not in script


def test_cancellation_ui_preserves_api_key_and_origin_contract() -> None:
    script = CANCELLATION_UI_JS

    assert "localStorage.getItem('ovllm.apikey.v1')" in script
    assert "headers.Authorization = `Bearer ${key}`" in script
    assert "target.sameOrigin" in script
    assert "target.path === STATUS_PATH" in script
    assert "method === 'GET'" in script
