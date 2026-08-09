from __future__ import annotations

import json
import time
from pathlib import Path

from playwright.sync_api import Page, expect


def _seed_chats(page: Page, chats: list[dict], active_id: str) -> None:
    payload = json.dumps({"chats": chats, "activeId": active_id})
    page.add_init_script(
        f"""
        const payload = {payload};
        localStorage.setItem('inferbridge.onboarding.auto-opened.v1', '1');
        localStorage.setItem('ovllm.chats.v2', JSON.stringify(payload.chats));
        localStorage.setItem('ovllm.activeChat.v2', payload.activeId);
        """
    )


def _chat(
    chat_id: str,
    title: str,
    updated: int,
    text: str,
    *,
    pinned: bool = False,
    archived: bool = False,
) -> dict:
    return {
        "id": chat_id,
        "title": title,
        "created": updated - 1000,
        "updated": updated,
        "pinned": pinned,
        "archived": archived,
        "messages": [
            {"role": "user", "content": text},
            {"role": "assistant", "content": f"Reply to {title}"},
        ],
    }


def test_conversation_search_and_management_actions(page: Page, inferbridge_url: str) -> None:
    now = int(time.time() * 1000)
    _seed_chats(
        page,
        [
            _chat(
                "chat-alpha",
                "Alpha plan",
                now,
                "The searchable needle is in this message.",
            ),
            _chat("chat-beta", "Beta notes", now - 5000, "Different content."),
            _chat(
                "chat-archived",
                "Archived needle",
                now - 6000,
                "needle inside archived content",
                archived=True,
            ),
        ],
        "chat-alpha",
    )
    page.goto(inferbridge_url, wait_until="networkidle")

    expect(page.locator(".cm-local-note")).to_contain_text("browser profile")
    page.locator("#conversation-search").fill("needle")
    expect(page.locator("#chats-list .chat-item")).to_have_count(1)
    expect(page.locator("#chats-list .chat-item-title")).to_contain_text("Alpha plan")
    expect(page.locator("#chats-list .chat-item-sub")).to_contain_text("needle")

    page.locator('[data-cm-view="archived"]').click()
    expect(page.locator("#chats-list .chat-item")).to_have_count(1)
    expect(page.locator("#chats-list .chat-item-title")).to_contain_text("Archived needle")
    expect(page.locator('[data-cm-view="archived"]')).to_have_attribute("aria-pressed", "true")

    page.locator('[data-cm-view="active"]').click()
    page.locator("#conversation-search-clear").click()
    page.get_by_label("Conversation actions: Alpha plan").click()
    page.get_by_role("menuitem", name="Pin", exact=True).click()
    stored = page.evaluate("JSON.parse(localStorage.getItem('ovllm.chats.v2'))")
    alpha = next(chat for chat in stored if chat["id"] == "chat-alpha")
    assert alpha["pinned"] is True

    page.get_by_label("Conversation actions: Alpha plan").click()
    page.get_by_role("menuitem", name="Rename", exact=True).click()
    expect(page.locator("#app")).to_have_js_property("inert", True)
    page.locator("#conversation-rename-input").fill("Renamed Alpha")
    page.locator("#conversation-rename-form").evaluate("form => form.requestSubmit()")
    expect(page.locator("#app")).to_have_js_property("inert", False)
    expect(
        page.locator('#chats-list [data-chat-id="chat-alpha"] .chat-item-title')
    ).to_contain_text("Renamed Alpha")
    assert page.evaluate("document.activeElement?.classList.contains('chat-item-more')") is True

    page.get_by_label("Conversation actions: Renamed Alpha").click()
    page.get_by_role("menuitem", name="Duplicate", exact=True).click()
    copy = page.evaluate("activeChat()")
    assert copy["id"] != "chat-alpha"
    assert copy["title"] == "Renamed Alpha copy"
    assert copy["messages"] == alpha["messages"]

    page.get_by_label("Conversation actions: Renamed Alpha copy").click()
    page.get_by_role("menuitem", name="Archive", exact=True).click()
    page.locator('[data-cm-view="archived"]').click()
    expect(page.locator("#chats-list .chat-item-title")).to_contain_text(
        "Renamed Alpha copy"
    )
    expect(page.locator("#chats-footer")).to_contain_text("local to this browser profile")


def test_json_import_export_retention_and_clear_all(
    page: Page,
    inferbridge_url: str,
    tmp_path: Path,
) -> None:
    now = int(time.time() * 1000)
    old = now - 45 * 86_400_000
    _seed_chats(
        page,
        [
            _chat("chat-current", "Current", now, "Current text"),
            _chat("chat-old", "Old removable", old, "Old text"),
            _chat("chat-pinned", "Old pinned", old, "Pinned text", pinned=True),
        ],
        "chat-current",
    )
    page.goto(inferbridge_url, wait_until="networkidle")

    with page.expect_download() as download_info:
        page.locator("#conversation-export-json").click()
    exported = json.loads(Path(download_info.value.path()).read_text(encoding="utf-8"))
    assert exported["format"] == "inferbridge-conversation"
    assert exported["schema_version"] == 1
    assert exported["application"] == "InferBridge"
    assert exported["conversation"]["title"] == "Current"
    assert exported["conversation"]["messages"][0] == {
        "role": "user",
        "content": "Current text",
    }
    assert "id" not in exported["conversation"]

    import_payload = {
        "format": "inferbridge-conversation",
        "schema_version": 1,
        "application": "InferBridge",
        "exported_at": "2026-08-09T00:00:00Z",
        "conversation": {
            "title": "Imported conversation",
            "created": 1000,
            "updated": 2000,
            "pinned": False,
            "archived": False,
            "model_id": "tinyllama-1.1b-chat-fp16",
            "system_prompt": "Imported system prompt",
            "messages": [
                {"role": "user", "content": "Imported question"},
                {"role": "assistant", "content": "Imported answer"},
            ],
        },
    }
    import_path = tmp_path / "inferbridge-chat.json"
    import_path.write_text(json.dumps(import_payload), encoding="utf-8")
    page.locator("#conversation-import-file").set_input_files(str(import_path))
    expect(page.locator("#workspace-chat-title")).to_have_text("Imported conversation")
    imported = page.evaluate("activeChat()")
    assert imported["id"] not in {"chat-current", "chat-old", "chat-pinned"}
    assert imported["messages"][0]["content"] == "Imported question"
    assert imported["modelId"] == "tinyllama-1.1b-chat-fp16"
    assert imported["systemPrompt"] == "Imported system prompt"

    page.once("dialog", lambda dialog: dialog.accept())
    page.locator("#conversation-retention").select_option("30")
    remaining_ids = set(page.evaluate("chats.map(chat => chat.id)"))
    assert "chat-old" not in remaining_ids
    assert "chat-pinned" in remaining_ids

    page.locator("#user-input").fill("draft that must be cleared")
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator("#conversation-clear-all").click()
    expect(page.locator('[data-cm-view="active"]')).to_contain_text("Active 1")
    assert page.evaluate("chats.length") == 1
    assert page.evaluate("activeChat().messages.length") == 0
    assert page.locator("#user-input").input_value() == ""
    assert page.evaluate("localStorage.getItem('ovllm.chatRetention.v1')") == "30"
    expect(page.locator(".cm-local-note")).to_contain_text("not synced")


def test_conversation_mutations_roll_back_when_chat_storage_fails(
    page: Page,
    inferbridge_url: str,
) -> None:
    now = int(time.time() * 1000)
    _seed_chats(
        page,
        [_chat("chat-current", "Current", now, "Current text")],
        "chat-current",
    )
    page.goto(inferbridge_url, wait_until="networkidle")

    page.evaluate(
        """
        window.__inferbridgeRealSetItem = Storage.prototype.setItem;
        window.__inferbridgeFailChatWrites = true;
        Storage.prototype.setItem = function(key, value) {
            if (window.__inferbridgeFailChatWrites && key === 'ovllm.chats.v2') {
                throw new DOMException('quota', 'QuotaExceededError');
            }
            return window.__inferbridgeRealSetItem.call(this, key, value);
        };
        """
    )

    page.get_by_label("Conversation actions: Current").click()
    page.get_by_role("menuitem", name="Pin", exact=True).click()
    assert page.evaluate("activeChat().pinned") is False
    expect(page.locator("#toast")).to_contain_text("Could not save chats")

    page.get_by_label("Conversation actions: Current").click()
    page.get_by_role("menuitem", name="Rename", exact=True).click()
    page.locator("#conversation-rename-input").fill("Unsaved rename")
    page.locator("#conversation-rename-form").evaluate("form => form.requestSubmit()")
    assert page.evaluate("activeChat().title") == "Current"
    expect(page.locator("#conversation-rename-modal")).to_have_attribute("aria-hidden", "false")
    page.locator("#conversation-rename-cancel").click()

    before = page.evaluate("chats.length")
    page.get_by_label("Conversation actions: Current").click()
    page.get_by_role("menuitem", name="Duplicate", exact=True).click()
    assert page.evaluate("chats.length") == before

    page.get_by_label("Conversation actions: Current").click()
    page.get_by_role("menuitem", name="Delete", exact=True).click()
    assert page.evaluate("chats.length") == before
    assert page.evaluate("activeChat().id") == "chat-current"
    expect(page.locator("#toast")).to_contain_text("was not deleted")

    page.evaluate("window.__inferbridgeFailChatWrites = false")


def test_archiving_active_chat_survives_retention_pruning_of_fallback(
    page: Page,
    inferbridge_url: str,
) -> None:
    now = int(time.time() * 1000)
    _seed_chats(
        page,
        [_chat("chat-current", "Current", now, "Current text")],
        "chat-current",
    )
    page.goto(inferbridge_url, wait_until="networkidle")

    old = now - 45 * 86_400_000
    page.evaluate(
        """
        ({ old }) => {
            localStorage.setItem('ovllm.chatRetention.v1', '30');
            chats.push({
                id: 'chat-old-fallback',
                title: 'Old fallback',
                created: old,
                updated: old,
                messages: [],
            });
            renderChatList();
        }
        """,
        {"old": old},
    )

    page.get_by_label("Conversation actions: Current").click()
    page.get_by_role("menuitem", name="Archive", exact=True).click()

    assert page.evaluate("activeChat().archived === true") is False
    assert page.evaluate("activeChat().messages.length") == 0
    assert page.evaluate(
        "chats.some(chat => chat.title === 'Current' && chat.archived === true)"
    ) is True
    assert page.evaluate("chats.some(chat => chat.id === 'chat-old-fallback')") is False
