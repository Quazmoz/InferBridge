from app.config import Settings  # noqa: F401 - installs composed UI extensions
from app.model_recovery_ui import MODEL_RECOVERY_UI_JS
from app.ui_extension import inject_multimodal_ui


def test_model_recovery_ui_is_injected_once_before_progress_controller() -> None:
    html = '<html><body><div class="chat-column"><div id="chat-area"></div></div></body></html>'

    rendered = inject_multimodal_ui(html)
    rendered_twice = inject_multimodal_ui(rendered)

    recovery = 'id="ovllm-model-recovery-extension"'
    cancellation = 'id="ovllm-model-cancellation-extension"'
    progress = 'id="ovllm-model-progress-extension"'
    assert rendered.count(recovery) == 1
    assert rendered_twice.count(recovery) == 1
    assert rendered.index(cancellation) < rendered.index(recovery) < rendered.index(progress)


def test_model_recovery_ui_exposes_all_requested_actions() -> None:
    script = MODEL_RECOVERY_UI_JS

    assert "Resume preparation" in script
    assert "Retry failed stage" in script
    assert "Restart from download" in script
    assert "Remove incomplete files" in script
    assert "View sanitized failure details" in script
    assert "downloaded_files" in script
    assert "conversion_output" in script
    assert "last_completed_stage" in script
    assert "recommended_action" in script


def test_model_recovery_ui_uses_stale_safe_identity_and_security_contract() -> None:
    script = MODEL_RECOVERY_UI_JS

    assert "recovery_id: activeRecovery.recovery_id" in script
    assert "model: activeRecovery.model_id" in script
    assert "localStorage.getItem('ovllm.apikey.v1')" in script
    assert "headers.Authorization = `Bearer ${key}`" in script
    assert "target.sameOrigin" in script
    assert "STATUS_PATHS.has(target.path)" in script
    assert "encodeURIComponent(activeRecovery.model_id)" in script


def test_model_recovery_ui_is_accessible_and_avoids_unsafe_html() -> None:
    script = MODEL_RECOVERY_UI_JS

    assert "setAttribute('role', 'dialog')" in script
    assert "setAttribute('aria-modal', 'true')" in script
    assert "aria-labelledby" in script
    assert "event.key === 'Escape'" in script
    assert "window.confirm(confirmation)" in script
    assert "textContent" in script
    assert "replaceChildren" in script
    assert "innerHTML" not in script
