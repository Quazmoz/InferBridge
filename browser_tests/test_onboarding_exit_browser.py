from __future__ import annotations

from playwright.sync_api import Page, expect


def test_broken_onboarding_exit_action_is_removed(
    page: Page,
    inferbridge_url: str,
) -> None:
    page.goto(inferbridge_url, wait_until="networkidle")

    shell = page.locator("#ovw-shell")
    expect(shell).to_have_count(1)
    expect(shell.locator("#ovw-close")).to_have_count(1)

    page.evaluate(
        """
        document.querySelector('#ovw-content').innerHTML =
            '<button data-action="exit">Exit</button>';
        """
    )

    expect(shell.locator('[data-action="exit"]')).to_have_count(0)
