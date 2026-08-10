from __future__ import annotations

import json
import time

from playwright.sync_api import Page, expect


MODEL_ID = "tinyllama-1.1b-chat-fp16"


def _load_mock_model(page: Page, inferbridge_url: str) -> None:
    response = page.request.post(f"{inferbridge_url}/v1/models/load", data={"model": MODEL_ID})
    assert response.ok, response.text()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        status = page.request.get(f"{inferbridge_url}/v1/system/status")
        assert status.ok, status.text()
        if MODEL_ID in status.json()["models"]["loaded"]:
            return
        time.sleep(0.05)
    raise AssertionError(f"Mock model {MODEL_ID} did not load in time")


def test_connection_hub_renders_copies_self_tests_and_preserves_feedback(
    page: Page,
    inferbridge_url: str,
) -> None:
    page.add_init_script(
        "localStorage.setItem('inferbridge.onboarding.auto-opened.v1', '1')"
    )
    page.goto(inferbridge_url, wait_until="networkidle")
    _load_mock_model(page, inferbridge_url)

    page.get_by_label("Toggle settings").click()
    page.locator("#connection-hub-open").click()
    expect(page.locator("#connection-hub-modal")).not_to_have_class("hidden")
    expect(page.locator("#ch-base-url")).to_have_text(f"{inferbridge_url}/v1")
    expect(page.locator("#ch-auth")).to_have_text("Authentication disabled")
    expect(page.locator("#ch-loaded-models")).to_contain_text(MODEL_ID)
    expect(page.locator("#ch-model-select")).to_have_value(MODEL_ID)

    page.context.grant_permissions(
        ["clipboard-read", "clipboard-write"], origin=inferbridge_url
    )
    page.get_by_label("Copy Base URL").first.click()
    expect(page.locator("#ch-copy-feedback")).to_have_text("Base URL copied.")
    assert page.evaluate("navigator.clipboard.readText()") == f"{inferbridge_url}/v1"

    page.locator("#ch-copy-model").click()
    expect(page.locator("#ch-copy-feedback")).to_have_text("Model ID copied.")
    assert page.evaluate("navigator.clipboard.readText()") == MODEL_ID
    expect(page.locator("#ch-python")).to_contain_text(
        f'base_url="{inferbridge_url}/v1"'
    )
    expect(page.locator("#ch-python")).to_contain_text('api_key="not-required"')
    expect(page.locator("#ch-python")).to_contain_text(f'model="{MODEL_ID}"')

    page.locator("#ch-run-test").click()
    expect(page.locator(".ch-test-status.running")).to_have_count(5)
    expect(page.locator(".ch-test-status.running")).to_have_count(0, timeout=15000)
    expect(page.locator(".ch-test-status.failed")).to_have_count(0)
    expect(page.locator(".ch-test-status.skipped")).to_have_count(0)
    expect(page.locator(".ch-test-status.passed")).to_have_count(5)
    expect(page.locator('[data-test-id="cancellation"] .ch-test-detail')).to_contain_text(
        "follow-up request"
    )
    expect(page.locator("#ch-message")).to_contain_text(
        "Review each check independently"
    )

    page.locator("#ch-done").click()
    expect(page.locator("#connection-hub-modal")).to_have_class("modal-overlay hidden")
    expect(page.locator("#connection-hub-open")).to_be_focused()
    page.locator("#connection-hub-open").click()
    expect(page.locator("#ch-base-url")).to_have_text(f"{inferbridge_url}/v1")
    expect(page.locator('[data-test-id="cancellation"] .ch-test-status')).to_have_text(
        "Passed"
    )

    def fail_self_test(route):
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "model_id": MODEL_ID,
                    "tests": [
                        {
                            "id": "models",
                            "label": "Model listing",
                            "status": "passed",
                            "duration_ms": 1,
                            "detail": "OK",
                        },
                        {
                            "id": "non_streaming",
                            "label": "Non-streaming generation",
                            "status": "failed",
                            "duration_ms": 2,
                            "detail": "Synthetic failure remains visible.",
                        },
                        {
                            "id": "streaming",
                            "label": "Streaming generation",
                            "status": "skipped",
                            "duration_ms": None,
                            "detail": "Skipped for UI test.",
                        },
                        {
                            "id": "cancellation",
                            "label": "Cancellation",
                            "status": "skipped",
                            "duration_ms": None,
                            "detail": "Skipped for UI test.",
                        },
                        {
                            "id": "authentication",
                            "label": "Authentication",
                            "status": "passed",
                            "duration_ms": 1,
                            "detail": "OK",
                        },
                    ],
                }
            ),
        )

    page.route("**/internal/connection-hub/self-test", fail_self_test)
    page.locator("#ch-run-test").click()
    expect(page.locator('[data-test-id="non_streaming"] .ch-test-status')).to_have_text(
        "Failed"
    )
    expect(page.locator('[data-test-id="non_streaming"] .ch-test-detail')).to_have_text(
        "Synthetic failure remains visible."
    )
    page.unroute("**/internal/connection-hub/self-test", fail_self_test)
