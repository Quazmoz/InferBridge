from __future__ import annotations

from playwright.sync_api import Page, expect


def test_onboarding_opens_without_broken_exit_action(
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
    page.goto(inferbridge_url, wait_until="networkidle")

    shell = page.locator("#ovw-shell")
    expect(shell).to_be_visible()
    expect(shell.locator('[data-action="exit"]')).to_have_count(0)
    expect(shell.locator("#ovw-close")).to_be_visible()
    expect(shell.locator('[data-action="continue"]')).to_be_visible()
