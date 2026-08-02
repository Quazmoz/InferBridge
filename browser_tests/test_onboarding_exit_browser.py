from __future__ import annotations

from playwright.sync_api import Page, expect


AUTO_OPEN_KEY = "inferbridge.onboarding.auto-opened.v1"


def test_onboarding_auto_opens_once_and_remains_manually_available(
    page: Page,
    inferbridge_url: str,
) -> None:
    page.route(
        "**/v1/onboarding/status",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=(
                '{"completed":false,"restart_requested":false,'
                '"rerun_scan_recommended":false,"recovery_message":null}'
            ),
        ),
    )
    page.route(
        "**/v1/onboarding/connection",
        lambda route: route.fulfill(
            status=409,
            content_type="application/json",
            body='{"detail":"Onboarding is incomplete"}',
        ),
    )
    page.goto(inferbridge_url, wait_until="networkidle")

    shell = page.locator("#ovw-shell")
    opener = page.locator("#ovw-open")
    expect(shell).to_be_visible()
    expect(shell.locator('[data-action="exit"]')).to_have_count(0)
    expect(shell.locator("#ovw-close")).to_be_visible()
    expect(shell.locator('[data-action="continue"]')).to_be_visible()
    assert page.evaluate("key => localStorage.getItem(key)", AUTO_OPEN_KEY) == "1"

    shell.locator("#ovw-close").click()
    page.reload(wait_until="networkidle")

    expect(shell).to_be_hidden()
    expect(opener).to_be_visible()
    expect(opener).to_have_text("Setup and onboarding")

    opener.click()
    expect(shell).to_be_visible()
    expect(shell.locator('[data-action="continue"]')).to_be_visible()
