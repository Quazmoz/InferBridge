"""Regression checks for the System Doctor diagnostics workflow."""

from app.config import Settings  # noqa: F401 - installs composed UI extensions
from app.ui_extension import inject_multimodal_ui


def test_system_doctor_is_injected_once_in_composed_ui() -> None:
    html = "<html><head></head><body></body></html>"

    rendered = inject_multimodal_ui(html)
    rendered_twice = inject_multimodal_ui(rendered)

    assert rendered.count('id="ovllm-system-doctor-extension"') == 1
    assert rendered.count('id="ovllm-system-doctor-extension-styles"') == 1
    assert rendered_twice.count('id="ovllm-system-doctor-extension"') == 1
    assert rendered_twice.count('id="ovllm-system-doctor-extension-styles"') == 1


def test_system_doctor_covers_requested_support_diagnostics() -> None:
    rendered = inject_multimodal_ui("<html><body></body></html>")

    assert "System Doctor" in rendered
    assert "fetch('/v1/system/status'" in rendered
    assert "fetch('/v1/devices'" in rendered
    assert "NPU is visible to OpenVINO" in rendered
    assert "driver/plugin may be missing or outdated" in rendered
    assert "No model has been converted yet" in rendered
    assert "Fallback routing active" in rendered
    assert "Copy support report" in rendered


def test_system_doctor_exposes_low_friction_feedback_without_uploading() -> None:
    rendered = inject_multimodal_ui("<html><body></body></html>")

    assert 'id="inferbridge-support-extension"' in rendered
    assert "https://consultant.quinnfavo.com/apps/inferbridge#feedback" in rendered
    assert "Send Feedback" in rendered
    assert "Nothing is uploaded automatically" in rendered
    assert "fetch(supportUrl" not in rendered


def test_system_doctor_distinguishes_fact_from_driver_inference() -> None:
    rendered = inject_multimodal_ui("<html><body></body></html>")

    assert "OpenVINO did not report an NPU" in rendered
    assert "may not have a supported NPU" in rendered
    assert "may be missing or outdated" in rendered
    assert "NPU driver is missing" not in rendered


def test_system_doctor_handles_mock_and_missing_telemetry_honestly() -> None:
    rendered = inject_multimodal_ui("<html><body></body></html>")

    assert "Conversion state is simulated in mock mode" in rendered
    assert "Mock mode does not verify local OpenVINO IR" in rendered
    assert "Memory telemetry unavailable" in rendered
    assert "Disk telemetry unavailable" in rendered


def test_system_doctor_is_keyboard_and_screen_reader_accessible() -> None:
    rendered = inject_multimodal_ui("<html><body></body></html>")

    assert 'role="dialog" aria-modal="true"' in rendered
    assert 'aria-labelledby="doctor-title"' in rendered
    assert "function focusables()" in rendered
    assert "e.key==='Escape'" in rendered
    assert "e.key!=='Tab'" in rendered
    assert "toggleAttribute('inert',value)" in rendered
    assert "returnFocus" in rendered


def test_support_report_omits_sensitive_browser_state() -> None:
    rendered = inject_multimodal_ui("<html><body></body></html>")
    # Isolate the doctor script block; other composed extensions (e.g. header overflow)
    # legitimately reference UI labels like "Export conversation" and must not leak in here.
    after_marker = rendered.split('id="ovllm-system-doctor-extension"', maxsplit=1)[1]
    extension = after_marker.split("</script>", maxsplit=1)[0]
    privacy_notice = "API keys, prompts, chat content, model errors, and local directory paths"

    assert privacy_notice in extension
    assert "settings-api-key" not in extension
    assert "status.disk?.models_dir" not in extension
    assert "conversation" not in extension
    assert "; error=${m.error}" not in extension
    assert "Copy failed:" in extension


def test_system_doctor_has_no_remote_runtime_dependencies() -> None:
    rendered = inject_multimodal_ui("<html><body></body></html>")

    assert "fonts.googleapis.com" not in rendered
    assert "cdn.jsdelivr.net" not in rendered
    assert "unpkg.com" not in rendered
