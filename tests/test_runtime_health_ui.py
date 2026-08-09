"""Source-level contract checks for the runtime model health center."""

from pathlib import Path

from app import runtime_health_ui


def test_runtime_health_ui_exposes_maintenance_workflow_without_remote_dependencies():
    source = Path(runtime_health_ui.__file__).read_text(encoding="utf-8")

    for token in (
        "runtime-health-modal",
        "/v1/runtime-health",
        "/v1/runtime-health/action",
        "/v1/runtime-health/batch",
        "Revalidate eligible",
        "Rebuild affected caches",
        "Reconvert",
        "Leave unchanged",
        "shared OpenVINO compiled cache",
        "Conversion provenance is never rewritten",
        "X-OV-LLM-UI",
    ):
        assert token in source

    assert "https://" not in runtime_health_ui._RUNTIME_HEALTH_JS
    assert "batch('reconvert'" not in runtime_health_ui._RUNTIME_HEALTH_JS


def test_runtime_health_ui_links_from_storage_and_model_library_surfaces():
    javascript = runtime_health_ui._RUNTIME_HEALTH_JS
    assert "#storage-manager-modal .sm-actions" in javascript
    assert "#model-library-modal .ml-footer-actions" in javascript
    assert "Model health" in javascript
