from __future__ import annotations

from app import storage_manager_ui, ui_extension


def test_storage_manager_ui_is_injected_once(monkeypatch) -> None:
    monkeypatch.setattr(ui_extension, "inject_multimodal_ui", lambda html: html)
    monkeypatch.delattr(ui_extension, "_STORAGE_MANAGER_UI_INSTALLED", raising=False)

    storage_manager_ui.install_storage_manager_ui_extension()
    first = ui_extension.inject_multimodal_ui("<html><body></body></html>")
    second = ui_extension.inject_multimodal_ui(first)

    assert first.count('id="ovllm-storage-manager-extension"') == 1
    assert second.count('id="ovllm-storage-manager-extension"') == 1
    assert "modal.id='storage-manager-modal'" in first
    assert "trigger.id='storage-manager-btn'" in first
    assert "/v1/storage/cleanup" in first
    assert "X-OV-LLM-UI" in first
    assert "ov-header-more-menu" in first
    assert "Storage and cache manager" in first
    assert "Transaction backups remain protected" in first
