from pathlib import Path

from app import errors
from app.desktop_onboarding import (
    DesktopOnboardingService,
    _STAGE_TIMEOUT_SECONDS,
    _classify_native_runtime_failure,
    _windows_build,
    actual_device_is_unresolved,
    augment_windows_scan,
    sanitize_system_scan,
)
from app.onboarding_models import PreparationStage, SystemScanResponse
from app.onboarding_service import PreparationJob
from runtime import device_check

ROOT = Path(__file__).resolve().parent.parent


def test_system_scan_does_not_expose_full_model_storage_path():
    scan = SystemScanResponse(
        generated_at="2026-01-01T00:00:00Z",
        fingerprint="abc",
        mock=False,
        items=[],
        hardware={
            "disk": {
                "free_gb": 100,
                "models_dir": r"C:\Users\private\AppData\Local\OpenVINOWindowsLLM\models",
            }
        },
    )

    sanitized = sanitize_system_scan(scan)

    assert sanitized.hardware["disk"] == {"free_gb": 100}
    assert "models_dir" in scan.hardware["disk"]


def test_windows_10_build_is_a_warning_not_an_unknown_failure():
    scan = SystemScanResponse(
        generated_at="2026-01-01T00:00:00Z",
        fingerprint="abc",
        mock=False,
        items=[],
        hardware={
            "os": {
                "system": "Windows",
                "release": "10",
                "version": "10.0.19045",
            }
        },
    )

    augmented = augment_windows_scan(scan, edition="Professional")
    build = next(item for item in augmented.items if item.key == "windows-build")
    edition = next(item for item in augmented.items if item.key == "windows-edition")

    assert build.status.value == "warning"
    assert edition.value == "Professional"
    assert any("older" in warning.lower() for warning in augmented.warnings)


def test_windows_build_uses_build_component_not_update_revision():
    assert _windows_build("10.0.26100.2454") == 26100


def test_openvino_capabilities_are_surfaced_when_available():
    scan = SystemScanResponse(
        generated_at="2026-01-01T00:00:00Z",
        fingerprint="abc",
        mock=False,
        items=[],
        hardware={
            "os": {"system": "Windows", "version": "10.0.26100"},
            "devices": [
                {
                    "device": "NPU",
                    "base": "NPU",
                    "optimization_capabilities": ["FP16", "INT8"],
                }
            ],
        },
    )

    augmented = augment_windows_scan(scan, edition="Professional")
    capabilities = next(item for item in augmented.items if item.key == "device-npu-capabilities")

    assert capabilities.value == "FP16, INT8"
    assert capabilities.status.value == "ready"


def test_composite_device_is_not_accepted_as_actual_hardware():
    assert actual_device_is_unresolved(None) is True
    assert actual_device_is_unresolved("AUTO") is True
    assert actual_device_is_unresolved("AUTO:NPU,GPU,CPU") is True
    assert actual_device_is_unresolved("MULTI:NPU,GPU,CPU") is True
    assert actual_device_is_unresolved("CPU") is False
    assert actual_device_is_unresolved("GPU.0") is False
    assert actual_device_is_unresolved("NPU") is False


def test_long_running_stages_have_distinct_generous_timeouts():
    assert _STAGE_TIMEOUT_SECONDS[PreparationStage.DOWNLOADING] >= 6 * 60 * 60
    assert _STAGE_TIMEOUT_SECONDS[PreparationStage.CONVERTING] >= 6 * 60 * 60
    assert _STAGE_TIMEOUT_SECONDS[PreparationStage.COMPILING] >= 60 * 60
    assert _STAGE_TIMEOUT_SECONDS[PreparationStage.BENCHMARKING] >= 10 * 60


def test_native_runtime_failure_is_classified_as_package_level(monkeypatch):
    monkeypatch.setattr(errors.sys, "frozen", True, raising=False)
    detail = (
        'Cannot add extension. Cannot find entry point to the extension library. '
        'Cannot load library "openvino_tokenizers.dll": 126'
    )
    job = PreparationJob(job_id="job", model_id="model", requested_device="NPU")
    job.error_detail = detail
    job.terminal(PreparationStage.FAILED, detail, error_code="preparation_failed")

    assert _classify_native_runtime_failure(job) is True
    assert job.error_code == "native_runtime_unavailable"
    assert "Reinstall the latest InferBridge build" in job.error_detail
    assert "falling back to CPU will not fix" in job.message


def test_native_runtime_failure_disables_retry_and_cpu_fallback(monkeypatch):
    monkeypatch.setattr(errors.sys, "frozen", True, raising=False)
    monkeypatch.setattr(device_check, "available_devices", lambda: ["CPU", "NPU"])
    detail = 'Cannot load library "openvino_tokenizers.dll": 126'
    job = PreparationJob(job_id="job", model_id="model", requested_device="NPU")
    job.error_detail = detail
    job.terminal(PreparationStage.FAILED, detail, error_code="preparation_failed")
    service = object.__new__(DesktopOnboardingService)
    service._jobs = {job.job_id: job}

    response = service.progress(job.job_id)

    assert response.error_code == "native_runtime_unavailable"
    assert response.can_retry is False
    assert response.can_fallback_to_cpu is False
    assert "package-level error" in response.error_detail


def test_normal_desktop_launch_cannot_silently_use_mock_runtime():
    source = (ROOT / "app" / "desktop_server.py").read_text(encoding="utf-8")
    assert "app.state.manager.force_mock and not mock" in source
    assert "Mock mode is never enabled" in source
