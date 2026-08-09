"""Source-level contract checks for the runtime model health center."""

import sys
from pathlib import Path

from app import model_library_ui, runtime_health_ui, storage_manager_ui, ui_extension


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


def test_runtime_health_ui_wraps_model_library_and_storage_manager(monkeypatch):
    monkeypatch.setattr(ui_extension, "inject_multimodal_ui", lambda html: html)
    monkeypatch.delattr(ui_extension, "_MODEL_LIBRARY_UI_EXTENSION_INSTALLED", raising=False)
    monkeypatch.delattr(ui_extension, "_STORAGE_MANAGER_UI_INSTALLED", raising=False)
    monkeypatch.delattr(ui_extension, "_RUNTIME_HEALTH_UI_EXTENSION_INSTALLED", raising=False)
    server = sys.modules.get("app.server")
    if server is not None:
        monkeypatch.setattr(server, "inject_multimodal_ui", server.inject_multimodal_ui)

    model_library_ui.install_model_library_ui_extension()
    storage_manager_ui.install_storage_manager_ui_extension()
    runtime_health_ui.install_runtime_health_ui_extension()

    page = ui_extension.inject_multimodal_ui("<html><body></body></html>")

    assert page.count('id="ovllm-model-library-extension"') == 1
    assert page.count('id="ovllm-storage-manager-extension"') == 1
    assert page.count('id="ovllm-runtime-health-extension"') == 1
    assert page.index('id="ovllm-model-library-extension"') < page.index(
        'id="ovllm-storage-manager-extension"'
    )
    assert page.index('id="ovllm-storage-manager-extension"') < page.index(
        'id="ovllm-runtime-health-extension"'
    )
