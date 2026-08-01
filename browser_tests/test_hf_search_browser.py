from __future__ import annotations

import json

from playwright.sync_api import Page, Route, expect


def test_hf_search_renders_untrusted_metadata_as_text(
    page: Page,
    inferbridge_url: str,
) -> None:
    model_id = "owner/model<script>alert(1)</script>"
    pipeline_tag = '<img src=x onerror="window.__hfInjected=true">'

    def fulfill_search(route: Route) -> None:
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                [
                    {
                        "id": model_id,
                        "downloads": "not-a-number",
                        "likes": None,
                        "pipeline_tag": pipeline_tag,
                        "backend": "javascript:invalid",
                    }
                ]
            ),
        )

    page.route("**/v1/models/search-hf*", fulfill_search)
    page.goto(inferbridge_url, wait_until="networkidle")
    page.locator("#add-model-btn").click()
    page.locator("#hf-search-input").fill("model")
    page.locator("#hf-search-btn").click()

    result = page.locator("#hf-search-results .search-result-item")
    expect(result).to_be_visible()
    expect(result.locator(".search-result-name")).to_have_text(model_id)
    expect(result.locator(".search-result-badge")).to_have_text(pipeline_tag)
    expect(result.locator(".search-result-meta")).to_contain_text("Downloads 0")
    expect(result.locator(".search-result-meta")).to_contain_text("Likes 0")
    assert page.locator("#hf-search-results script, #hf-search-results img").count() == 0
    assert page.evaluate("window.__hfInjected") is None

    result.locator(".search-result-select-btn").click()
    expect(page.locator("#modal-panel-manual")).to_be_visible()
    expect(page.locator("#custom-source-model")).to_have_value(model_id)
    expect(page.locator("#custom-backend")).to_have_value("openvino-genai")
    assert page.evaluate("window.__hfInjected") is None


def test_hf_search_does_not_echo_server_error_details(
    page: Page,
    inferbridge_url: str,
) -> None:
    private_detail = '<img src=x onerror="window.__hfErrorInjected=true"> secret-token'

    def fail_search(route: Route) -> None:
        route.fulfill(
            status=500,
            content_type="application/json",
            body=json.dumps({"detail": private_detail}),
        )

    page.route("**/v1/models/search-hf*", fail_search)
    page.goto(inferbridge_url, wait_until="networkidle")
    page.locator("#add-model-btn").click()
    page.locator("#hf-search-input").fill("private-model")
    page.locator("#hf-search-btn").click()

    error = page.locator("#hf-search-results .search-empty.error")
    expect(error).to_be_visible()
    expect(error).to_contain_text("Search failed")
    expect(error).not_to_contain_text("secret-token")
    assert page.locator("#hf-search-results img").count() == 0
    assert page.evaluate("window.__hfErrorInjected") is None
    expect(page.locator("#hf-search-btn")).to_be_enabled()


def test_hf_search_auth_recovery_closes_modal_before_opening_settings(
    page: Page,
    inferbridge_url: str,
) -> None:
    def require_auth(route: Route) -> None:
        route.fulfill(
            status=401,
            content_type="application/json",
            body=json.dumps({"detail": "API key required"}),
        )

    page.route("**/v1/models/search-hf*", require_auth)
    page.goto(inferbridge_url, wait_until="networkidle")
    page.locator("#add-model-btn").click()
    page.locator("#hf-search-input").fill("gated-model")
    page.locator("#hf-search-btn").click()

    expect(page.locator("#custom-model-modal")).to_be_hidden()
    expect(page.locator("#settings-sidebar")).to_be_visible()
    expect(page.locator("#device-label")).to_have_text("Auth required")
    assert page.evaluate("document.activeElement?.id") == "settings-api-key"
