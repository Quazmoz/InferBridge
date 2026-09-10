from __future__ import annotations

from playwright.sync_api import Page, expect


def _open_benchmark_lab(page: Page, inferbridge_url: str) -> None:
    page.goto(inferbridge_url, wait_until="networkidle")
    advisor = page.locator("#advisor-open-btn")
    if not advisor.is_visible():
        page.locator("#ov-header-more-btn").click()
    advisor.click()
    expect(page.locator("#advisor-dialog")).to_be_visible()
    page.locator("#advisor-tab-benchmark").click()
    expect(page.locator("#advisor-panel-benchmark")).to_be_visible()


def test_benchmark_lab_runs_in_mock_mode_and_keeps_legacy_panel_hidden(
    page: Page,
    inferbridge_url: str,
) -> None:
    _open_benchmark_lab(page, inferbridge_url)

    legacy = page.locator("#benchmark-devices").locator(
        "xpath=ancestor::*[contains(@class,'benchmark-panel')][1]"
    )
    expect(legacy).to_be_hidden()
    expect(page.locator("#benchmark-run-lab-btn")).to_be_enabled()

    # The native radio is visually hidden (opacity:0; pointer-events:none) behind its
    # styled label, so drive it the way a user does rather than checking the input.
    page.locator('label.benchmark-preset:has(input[value="quick"])').click()
    expect(page.locator('input[name="benchmark-preset"][value="quick"]')).to_be_checked()
    page.locator("#benchmark-run-lab-btn").click()

    expect(page.locator(".benchmark-progress")).to_be_visible()
    expect(page.locator(".benchmark-results")).to_be_visible(timeout=20_000)
    expect(page.locator(".benchmark-synthetic")).to_contain_text("Synthetic / mock mode")
    # The hidden legacy panel keeps its own .benchmark-table, so scope to the Lab panel.
    lab_table = page.locator("#advisor-panel-benchmark .benchmark-table")
    expect(lab_table.locator("tbody tr")).to_have_count(1)
    expect(lab_table.locator("tbody")).to_contain_text("CPU → CPU")
    expect(page.locator("#benchmark-copy-results")).to_be_visible()
    expect(page.locator("#benchmark-download-json")).to_be_visible()


def test_benchmark_lab_is_usable_on_narrow_viewport(
    page: Page,
    inferbridge_url: str,
) -> None:
    page.set_viewport_size({"width": 390, "height": 844})
    _open_benchmark_lab(page, inferbridge_url)

    expect(page.locator("#advisor-dialog")).to_be_visible()
    expect(page.locator("#benchmark-model-search")).to_be_visible()
    expect(page.locator("#benchmark-run-lab-btn")).to_be_visible()
    box = page.locator("#advisor-dialog").bounding_box()
    assert box is not None
    assert box["width"] <= 390
