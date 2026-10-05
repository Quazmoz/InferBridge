from __future__ import annotations

import json

import pytest
from playwright.sync_api import Page, expect

MODEL_ID = "tinyllama-1.1b-chat-fp16"


@pytest.mark.parametrize("ending", ["truncated", "invalid_json"])
def test_interrupted_reply_is_preserved_and_next_request_works(
    page: Page, inferbridge_url: str, ending: str
) -> None:
    page.add_init_script("localStorage.setItem('inferbridge.onboarding.auto-opened.v1', '1')")
    page.goto(inferbridge_url, wait_until="networkidle")
    response = page.request.post(f"{inferbridge_url}/v1/models/load", data={"model": MODEL_ID})
    assert response.ok
    page.wait_for_function("model => availableModels.get(model)?.is_loaded === true", arg=MODEL_ID)
    page.locator("#model-select").select_option(MODEL_ID)

    complete = False

    def reply(route):
        text = "Complete reply" if complete else "Partial reply"
        body = "data: " + json.dumps({"choices": [{"delta": {"content": text}}]}) + "\n\n"
        if complete:
            body += "data: [DONE]\n\n"
        elif ending == "invalid_json":
            body += "data: {invalid json}\n\ndata: [DONE]\n\n"
        route.fulfill(status=200, content_type="text/event-stream", body=body)

    page.route("**/v1/chat/completions", reply)
    page.locator("#user-input").fill("First question")
    page.locator("#send-btn").click()
    expect(page.get_by_text("Partial reply", exact=True)).to_be_visible()
    expect(page.get_by_role("alert").filter(has_text="Error:")).to_be_visible()
    expect(page.locator("#send-btn")).to_have_attribute("aria-label", "Send message")
    stored = page.evaluate("JSON.parse(localStorage.getItem('ovllm.chats.v2'))")
    assert any(
        message.get("content") == "Partial reply" for chat in stored for message in chat["messages"]
    )

    complete = True
    page.locator("#user-input").fill("Second question")
    page.locator("#send-btn").click()
    expect(page.get_by_text("Complete reply", exact=True)).to_be_visible()
    expect(page.locator("#send-btn")).to_have_attribute("aria-label", "Send message")
