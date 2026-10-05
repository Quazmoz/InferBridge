from __future__ import annotations

from app.advisor_ui import ADVISOR_EXTENSION_JS


def test_benchmark_ui_does_not_coerce_missing_metrics_to_zero():
    assert "formatOptionalMs" in ADVISOR_EXTENSION_JS
    assert "formatOptionalGb" in ADVISOR_EXTENSION_JS
    assert "value === null || value === undefined || value === ''" in ADVISOR_EXTENSION_JS


def test_loaded_model_defaults_to_its_existing_direct_device():
    assert "safeBenchmarkDefaults" in ADVISOR_EXTENSION_JS
    assert "latestStatus?.device?.loaded?.[modelId]" in ADVISOR_EXTENSION_JS
    assert "benchmarkSelectedDevices = new Set([directDevice])" in ADVISOR_EXTENSION_JS
