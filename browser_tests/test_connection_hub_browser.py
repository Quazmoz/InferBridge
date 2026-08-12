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


def _passed_self_test() -> dict:
    labels = {
        "models": "Model listing",
        "non_streaming": "Non-streaming generation",
        "streaming": "Streaming generation",
        "cancellation": "Cancellation",
        "authentication": "Authentication",
    }
    return {
        "model_id": MODEL_ID,
        "tests": [
            {
                "id": test_id,
                "label": label,
                "status": "passed",
                "duration_ms": 1,
                "detail": "OK",
            }
            for test_id, label in labels.items()
        ],
    }


def test_connection_hub_renders_copies_self_tests_and_preserves_feedback(
    page: Page,
    inferbridge_url: str,
) -> None:
    page.add_init_script("localStorage.setItem('inferbridge.onboarding.auto-opened.v1', '1')")
    page.goto(inferbridge_url, wait_until="networkidle")
    _load_mock_model(page, inferbridge_url)

    page.get_by_label("Toggle settings").click()
    page.locator("#connection-hub-open").click()
    expect(page.locator("#connection-hub-modal")).not_to_have_class("hidden")
    expect(page.locator("#ch-base-url")).to_have_text(f"{inferbridge_url}/v1")
    expect(page.locator("#ch-auth")).to_have_text("Authentication disabled")
    expect(page.locator("#ch-loaded-models")).to_contain_text(MODEL_ID)
    expect(page.locator("#ch-model-select")).to_have_value(MODEL_ID)

    page.context.grant_permissions(["clipboard-read", "clipboard-write"], origin=inferbridge_url)
    page.get_by_label("Copy Base URL").first.click()
    expect(page.locator("#ch-copy-feedback")).to_have_text("Base URL copied.")
    assert page.evaluate("navigator.clipboard.readText()") == f"{inferbridge_url}/v1"

    page.locator("#ch-copy-model").click()
    expect(page.locator("#ch-copy-feedback")).to_have_text("Model ID copied.")
    assert page.evaluate("navigator.clipboard.readText()") == MODEL_ID
    expect(page.locator("#ch-python")).to_contain_text(f'base_url="{inferbridge_url}/v1"')
    expect(page.locator("#ch-python")).to_contain_text('api_key="not-required"')
    expect(page.locator("#ch-python")).to_contain_text(f'model="{MODEL_ID}"')

    page.locator("#ch-run-test").click()
    expect(page.locator(".ch-test-status.running")).to_have_count(0, timeout=15000)
    expect(page.locator(".ch-test-status.failed")).to_have_count(0)
    expect(page.locator(".ch-test-status.skipped")).to_have_count(0)
    expect(page.locator(".ch-test-status.passed")).to_have_count(5)
    expect(page.locator('[data-test-id="cancellation"] .ch-test-detail')).to_contain_text(
        "follow-up request"
    )
    expect(page.locator("#ch-message")).to_contain_text("Review each check independently")

    page.locator("#ch-done").click()
    expect(page.locator("#connection-hub-modal")).to_have_class("modal-overlay hidden")
    expect(page.locator("#connection-hub-open")).to_be_focused()
    page.locator("#connection-hub-open").click()
    expect(page.locator("#ch-base-url")).to_have_text(f"{inferbridge_url}/v1")
    expect(page.locator('[data-test-id="cancellation"] .ch-test-status')).to_have_text("Passed")

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
    expect(page.locator('[data-test-id="non_streaming"] .ch-test-status')).to_have_text("Failed")
    expect(page.locator('[data-test-id="non_streaming"] .ch-test-detail')).to_have_text(
        "Synthetic failure remains visible."
    )
    page.unroute("**/internal/connection-hub/self-test", fail_self_test)


def test_connection_hub_requires_and_sends_existing_browser_api_credential(
    page: Page,
    inferbridge_url: str,
) -> None:
    page.add_init_script("localStorage.setItem('inferbridge.onboarding.auto-opened.v1', '1')")
    metadata = {
        "runtime_state": "available",
        "base_url": f"{inferbridge_url}/v1",
        "api_root": "/v1",
        "listen_host": "127.0.0.1",
        "port": int(inferbridge_url.rsplit(":", 1)[1]),
        "authentication": {
            "enabled": True,
            "required": True,
            "label": "Authentication required",
            "api_key_placeholder": "YOUR_INFERBRIDGE_API_KEY",
        },
        "models": [
            {
                "id": MODEL_ID,
                "name": "TinyLlama",
                "backend": "openvino-genai",
                "status": "loaded",
                "loaded": True,
                "generation_capable": True,
                "busy": False,
            }
        ],
        "loaded_model_ids": [MODEL_ID],
        "usable_model_ids": [MODEL_ID],
        "lan": {
            "enabled": False,
            "classification": "loopback",
            "label": "Local only",
            "detail": "Other devices cannot connect to this listener.",
            "configured_host": "127.0.0.1",
            "url": None,
            "security_attention": False,
        },
    }
    seen_authorization: list[str | None] = []

    page.route(
        "**/internal/connection-hub",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(metadata),
        ),
    )

    def authenticated_self_test(route):
        seen_authorization.append(route.request.headers.get("authorization"))
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(_passed_self_test()),
        )

    page.route("**/internal/connection-hub/self-test", authenticated_self_test)
    page.goto(inferbridge_url, wait_until="networkidle")
    page.get_by_label("Toggle settings").click()
    page.locator("#connection-hub-open").click()
    expect(page.locator("#ch-auth")).to_have_text("Authentication required")

    page.locator("#ch-run-test").click()
    expect(page.locator("#ch-message")).to_contain_text(
        "Close the Hub, enter the InferBridge API key"
    )
    assert seen_authorization == []

    page.locator("#ch-close").click()
    page.evaluate("localStorage.setItem('ovllm.apikey.v1', 'browser-only-secret')")
    page.locator("#connection-hub-open").click()
    page.locator("#ch-run-test").click()

    expect(page.locator(".ch-test-status.running")).to_have_count(0, timeout=5000)
    expect(page.locator(".ch-test-status.passed")).to_have_count(5)
    assert seen_authorization == ["Bearer browser-only-secret"]
    expect(page.locator("#ch-value-key")).to_have_text("YOUR_INFERBRIDGE_API_KEY")
    expect(page.locator("#ch-python")).not_to_contain_text("browser-only-secret")
