from app.config import Settings  # noqa: F401 - installs composed UI extensions
from app.onboarding_ui import ONBOARDING_JS
from app.ui_extension import inject_multimodal_ui


def test_desktop_wizard_is_injected_once_without_forcing_npu():
    html = "<html><head></head><body></body></html>"
    rendered = inject_multimodal_ui(html)
    rendered_twice = inject_multimodal_ui(rendered)
    marker = 'id="ovllm-desktop-onboarding-extension"'
    assert rendered.count(marker) == 1
    assert rendered_twice.count(marker) == 1
    assert "ovllm.first-npu-ready.v1" not in rendered
    assert "JSON.stringify({ model: FIRST_TEST_MODEL_ID, device: 'NPU' })" not in rendered


def test_wizard_has_accessible_stages_and_real_connection_configuration():
    rendered = inject_multimodal_ui("<html><head></head><body></body></html>")
    for label in (
        "System scan",
        "NPU readiness",
        "Recommended model",
        "Advanced details",
        "Change model storage location",
        "Downloading model files",
        "Converting or quantizing to OpenVINO",
        "Compiling for the selected device",
        "Running a short benchmark",
        "Troubleshooting details",
        "Change device",
        "Benchmark verification",
        "Actual device",
        "Time to first token",
        "Generation throughput",
        "Completion tokens",
        "Deterministic mock",
        "Measured on the active runtime",
        "Your local benchmark",
        "OpenAI Python client",
        "Open WebUI",
        "n8n",
    ):
        assert label in rendered
    assert "aria-live" in rendered
    assert "role','progressbar" in rendered
    assert "opener.hidden=false" in rendered
    assert "https://consultant.quinnfavo.com/apps/inferbridge#feedback" in rendered


def test_wizard_auto_opens_once_and_keeps_manual_onboarding_access():
    assert "inferbridge.onboarding.auto-opened.v1" in ONBOARDING_JS
    assert "function hasAutoOpened()" in ONBOARDING_JS
    assert "function markAutoOpened()" in ONBOARDING_JS
    assert "if(hasAutoOpened())return;markAutoOpened();show()" in ONBOARDING_JS
    assert "Setup and onboarding" in ONBOARDING_JS
    assert "Onboarding needs attention" in ONBOARDING_JS
    assert "Restart onboarding" in ONBOARDING_JS
    assert "opener.addEventListener('click'" in ONBOARDING_JS


def test_wizard_omits_broken_exit_action_but_keeps_close():
    assert "id:'exit'" not in ONBOARDING_JS
    assert "action==='exit'" not in ONBOARDING_JS
    assert "async function exitApp" not in ONBOARDING_JS
    assert 'id="ovw-close"' in ONBOARDING_JS
    assert "label:'Documentation'" in ONBOARDING_JS
    assert "label:'Continue'" in ONBOARDING_JS
