from __future__ import annotations

from app import conversation_management_ui, ui_extension


def test_conversation_management_ui_is_injected_once(monkeypatch) -> None:
    monkeypatch.setattr(ui_extension, "inject_multimodal_ui", lambda html: html)
    monkeypatch.delattr(
        ui_extension,
        "_CONVERSATION_MANAGEMENT_EXTENSION_INSTALLED",
        raising=False,
    )

    conversation_management_ui.install_conversation_management_extension()
    first = ui_extension.inject_multimodal_ui("<html><body></body></html>")
    second = ui_extension.inject_multimodal_ui(first)

    assert first.count('id="ovllm-conversation-management-extension"') == 1
    assert second.count('id="ovllm-conversation-management-extension"') == 1
    assert "Search conversation titles and messages" in first
    assert "Conversation storage" in first
    assert "inferbridge-conversation" in first
    assert "Export Markdown" in first
    assert "Export JSON" in first
    assert "Import JSON" in first
    assert "Clear all local chats" in first
    assert "local to this browser profile" in first
    assert "pendingModelId" in first


def test_conversation_management_keeps_existing_chat_storage_schema() -> None:
    script = conversation_management_ui.CONVERSATION_MANAGEMENT_JS

    assert "ovllm.chatRetention.v1" in script
    assert "CHATS_KEY" in script
    assert "ACTIVE_CHAT_KEY" in script
    assert "localStorage.setItem(CHATS_KEY" in script
    assert "localStorage.removeItem(LEGACY_STORAGE_KEY)" in script
    assert "pinned" in script
    assert "archived" in script
    assert "manualTitle" in script
