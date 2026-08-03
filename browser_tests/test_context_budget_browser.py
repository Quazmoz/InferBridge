from __future__ import annotations

import json

from playwright.sync_api import Page, expect

MODEL_ID = "tinyllama-1.1b-chat-fp16"


def _status_payload() -> dict:
    return {
        "schema_version": 1,
        "generated_at": 1785669600,
        "device": {
            "default": "CPU",
            "mock": True,
            "available": ["CPU"],
            "loaded": {MODEL_ID: "MOCK"},
            "busy": False,
        },
        "models": {
            "loaded": [MODEL_ID],
            "count": 1,
            "loading_count": 0,
            "available": [
                {
                    "id": MODEL_ID,
                    "name": "TinyLlama 1.1B Chat",
                    "description": "Browser context test model",
                    "status": "loaded",
                    "status_label": "Loaded",
                    "is_loaded": True,
                    "is_loading": False,
                    "is_downloaded": True,
                    "device": "MOCK",
                    "recommended_device": "CPU",
                    "max_context_len": 2048,
                    "max_output_tokens": 512,
                    "backend": "openvino-genai",
                    "supports_vision": False,
                    "capabilities": ["chat"],
                    "can_load": False,
                    "can_convert": False,
                    "can_unload": True,
                    "can_delete": False,
                }
            ],
        },
    }


def _budget_payload() -> dict:
    return {
        "model": MODEL_ID,
        "model_name": "TinyLlama 1.1B Chat",
        "prompt_tokens": 1720,
        "max_prompt_tokens": 1536,
        "prompt_budget_percent": 112.0,
        "max_context_tokens": 2048,
        "model_output_reserve_tokens": 512,
        "requested_output_tokens": 512,
        "available_output_tokens": 320,
        "effective_output_tokens": 320,
        "output_limited": True,
        "safety_tokens": 8,
        "context_usage_tokens": 2048,
        "context_usage_percent": 100.0,
        "message_count": 7,
        "retained_message_count": 3,
        "dropped_message_count": 4,
        "dropped_turn_count": 2,
        "dropped_messages": [
            {"index": 1, "role": "user", "preview": "An older question that will be omitted."},
            {"index": 2, "role": "assistant", "preview": "Its older answer will also be omitted."},
        ],
        "dropped_preview_truncated": True,
        "will_truncate": True,
        "prompt_over_budget": True,
        "blocked": False,
        "system_instructions_retained": True,
        "attachment_count": 0,
        "attachment_token_estimate": 0,
        "attachment_estimate_per_image": 512,
    }


def test_context_budget_chip_previews_omissions_and_reduces_output(
    page: Page,
    inferbridge_url: str,
) -> None:
    page.add_init_script("localStorage.setItem('inferbridge.onboarding.auto-opened.v1', '1')")
    page.goto(inferbridge_url, wait_until="networkidle")

    submitted = []
    page.route(
        "**/v1/models/status",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(_status_payload()),
        ),
    )

    def context_budget(route) -> None:
        submitted.append(json.loads(route.request.post_data or "{}"))
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(_budget_payload()),
        )

    page.route("**/v1/chat/context-budget", context_budget)
    page.evaluate("fetch('/v1/models/status').then(response => response.json())")

    model_select = page.locator("#model-select")
    expect(model_select).to_have_value(MODEL_ID)
    page.locator("#user-input").fill("Keep this current draft in the budget calculation.")

    chip = page.locator("#ov-context-budget-chip")
    expect(chip).to_contain_text("Context 1,720 / 1,536")
    expect(chip).to_contain_text("2 turns omitted")
    expect(chip).to_have_attribute("data-state", "danger")

    chip.click()
    panel = page.locator("#ov-context-budget-panel")
    expect(panel).to_be_visible()
    expect(panel).to_contain_text("Prompt budget")
    expect(panel).to_contain_text("Available output")
    expect(panel).to_contain_text("2 older turns will be omitted")
    expect(panel).to_contain_text("Omitted message preview")
    expect(panel).to_contain_text("An older question that will be omitted.")
    expect(panel).to_contain_text("Additional omitted messages are not shown")

    panel.get_by_role("button", name="Reduce output to fit").click()
    expect(page.locator("#settings-max-tokens")).to_have_value("320")

    assert submitted
    latest = submitted[-1]
    assert latest["model"] == MODEL_ID
    assert latest["max_tokens"] in {320, 512}
    assert latest["messages"][-1] == {
        "role": "user",
        "content": "Keep this current draft in the budget calculation.",
    }
    assert latest["image_count"] == 0
