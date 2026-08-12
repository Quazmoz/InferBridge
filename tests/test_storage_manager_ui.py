from __future__ import annotations

from app import storage_manager_ui, ui_registry

_STORAGE_MANAGER = ("ovllm-storage-manager-extension",)


def test_storage_manager_ui_is_injected_once() -> None:
    storage_manager_ui.install_storage_manager_ui_extension()
    first = ui_registry.render_only("<html><body></body></html>", _STORAGE_MANAGER)
    second = ui_registry.render_only(first, _STORAGE_MANAGER)

    assert first.count('id="ovllm-storage-manager-extension"') == 1
    assert second.count('id="ovllm-storage-manager-extension"') == 1
    assert "modal.id='storage-manager-modal'" in first
    assert "trigger.id='storage-manager-btn'" in first
    assert "/v1/storage/cleanup" in first
    assert "X-OV-LLM-UI" in first
    assert "ov-header-more-menu" in first
    assert "ov-header-more-btn" in first
    assert "Storage and cache manager" in first
    assert "Transaction backups remain protected" in first
