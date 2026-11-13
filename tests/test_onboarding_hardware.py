from app.onboarding_hardware import build_system_scan, classify_npu_readiness
from app.onboarding_models import NpuState


def snapshot(*, mock=False, devices=None, cpu="Intel CPU"):
    devices = devices or []
    return {
        "generated_at": "2026-01-01T00:00:00+00:00",
        "fingerprint": "abc",
        "os": {"system": "Windows", "release": "11", "version": "10.0.26100"},
        "cpu": {
            "name": cpu,
            "architecture": "AMD64",
            "physical_cores": 8,
            "logical_cores": 16,
        },
        "memory": {"total_gb": 32, "available_gb": 20},
        "disk": {"free_gb": 100},
        "available_devices": devices,
        "devices": [
            {"device": item, "base": item.split(".")[0], "full_name": item} for item in devices
        ],
        "runtime": {"openvino": "2026.2", "openvino_genai": "2026.2", "mock": mock},
    }


def test_npu_visible_is_usable():
    result = classify_npu_readiness(snapshot(devices=["CPU", "NPU"]), pnp_devices=[])
    assert result.state is NpuState.USABLE
    assert result.usable is True


def test_hardware_without_plugin_is_distinct_from_no_npu():
    result = classify_npu_readiness(
        snapshot(devices=["CPU"]),
        pnp_devices=[{"name": "Intel AI Boost", "status": "OK", "instance_id": "redacted"}],
    )
    assert result.state is NpuState.HARDWARE_PLUGIN_UNAVAILABLE
    assert result.fallback_device == "CPU"


def test_linux_npu_without_plugin_names_the_doctor_finding_and_command():
    data = snapshot(devices=["CPU"])
    data["os"] = {"system": "Linux", "release": "6.8.0"}
    finding = "Intel NPU user-space driver missing: Level Zero loader (libze_loader.so.1)"
    result = classify_npu_readiness(
        data,
        pnp_devices=[
            {"name": "Intel NPU (Meteor Lake): kernel driver intel_vpu", "status": "ok"},
            {"name": finding, "status": "error"},
        ],
    )
    assert result.state is NpuState.HARDWARE_PLUGIN_UNAVAILABLE
    assert finding in result.explanation and "kernel driver" not in result.explanation
    assert "inferbridge doctor" in result.explanation and "Windows" not in result.explanation


def test_mock_mode_never_claims_hardware_support():
    result = classify_npu_readiness(snapshot(mock=True, devices=["CPU", "NPU"]), pnp_devices=[])
    assert result.state is NpuState.MOCK
    assert result.usable is False


def test_unknown_scan_values_are_not_failures():
    data = snapshot(devices=[])
    data["memory"] = {}
    scan = build_system_scan(data)
    item = next(row for row in scan.items if row.key == "ram-total")
    assert item.status.value == "unknown"
