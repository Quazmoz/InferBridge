from __future__ import annotations

from playwright.sync_api import Page, expect


def _active_element_id(page: Page) -> str:
    return page.evaluate("document.activeElement?.id || ''")


def _enabled_menu_button_ids(page: Page) -> list[str]:
    return page.evaluate(
        """
        Array.from(document.querySelectorAll('#ov-header-more-menu [role="menuitem"]'))
            .filter(button => !button.disabled)
            .map(button => button.id)
        """
    )


def test_compact_header_menu_supports_complete_keyboard_navigation(
    page: Page,
    inferbridge_url: str,
) -> None:
    page.set_viewport_size({"width": 700, "height": 900})
    page.goto(inferbridge_url, wait_until="networkidle")

    trigger = page.locator("#ov-header-more-btn")
    menu = page.locator("#ov-header-more-menu")
    expect(trigger).to_be_visible()
    expect(menu).to_be_hidden()

    trigger.focus()
    page.keyboard.press("ArrowDown")
    expect(menu).to_be_visible()
    expect(trigger).to_have_attribute("aria-expanded", "true")

    enabled_ids = _enabled_menu_button_ids(page)
    assert len(enabled_ids) >= 2
    assert _active_element_id(page) == enabled_ids[0]

    page.keyboard.press("ArrowDown")
    assert _active_element_id(page) == enabled_ids[1]

    page.keyboard.press("End")
    assert _active_element_id(page) == enabled_ids[-1]

    page.keyboard.press("Home")
    assert _active_element_id(page) == enabled_ids[0]

    page.keyboard.press("Tab")
    expect(menu).to_be_hidden()
    expect(trigger).to_have_attribute("aria-expanded", "false")
    assert _active_element_id(page) == "settings-toggle-btn"


def test_compact_header_actions_close_without_losing_focus(
    page: Page,
    inferbridge_url: str,
) -> None:
    page.set_viewport_size({"width": 700, "height": 900})
    page.goto(inferbridge_url, wait_until="networkidle")

    trigger = page.locator("#ov-header-more-btn")
    menu = page.locator("#ov-header-more-menu")
    theme_item = page.locator(".ov-header-overflow-item").filter(
        has=page.locator("#theme-toggle-btn")
    )

    trigger.click()
    theme_before = page.locator("html").get_attribute("data-theme")
    theme_item.locator(".ov-header-overflow-label").click()

    expect(menu).to_be_hidden()
    assert _active_element_id(page) == "ov-header-more-btn"
    assert page.locator("html").get_attribute("data-theme") != theme_before

    trigger.click()
    page.locator("#add-model-btn").click()

    expect(menu).to_be_hidden()
    expect(page.locator("#custom-model-modal")).to_be_visible()
    assert _active_element_id(page) == "hf-search-input"
