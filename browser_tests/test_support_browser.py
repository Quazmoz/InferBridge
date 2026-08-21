from __future__ import annotations

from playwright.sync_api import Page, expect

from app.support import SUPPORT_URL


def test_system_doctor_exposes_feedback_link_without_uploading(
    page: Page,
    inferbridge_url: str,
) -> None:
    page.add_init_script("localStorage.setItem('inferbridge.onboarding.auto-opened.v1', '1')")
    page.goto(inferbridge_url, wait_until="networkidle")

    page.locator("#doctor-btn").click()

    feedback = page.locator("#doctor-feedback")
    expect(feedback).to_be_visible()
    expect(feedback).to_have_attribute("href", SUPPORT_URL)
    expect(feedback).to_have_attribute("target", "_blank")
    expect(feedback).to_have_attribute("rel", "noopener noreferrer")
    expect(page.locator("#doctor-modal .doctor-footer-note")).to_contain_text(
        "Nothing is uploaded automatically"
    )
