"""Browser coverage for the two capability-gated desktop surfaces.

The storage manager and the runtime health center previously existed only if
``app.desktop_server`` was the module that imported them, so neither could be reached from
the development server and neither had a browser test. They are now always registered and
gated by an explicit capability, which is what makes this file possible.

The plain server does not register ``/v1/storage`` or ``/v1/runtime-health``, so these tests
also confirm the surfaces degrade honestly rather than breaking when their routes are absent.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from app.ui_composition import DESKTOP_CAPABILITY
from app.ui_registry import activate, deactivate


@pytest.fixture()
def desktop_page(page: Page, inferbridge_url: str) -> Page:
    """Serve the desktop composition for one test, then restore the ordinary one.

    The composition is process state and the document is composed per request, so turning
    the capability on is enough for the already-running test server to serve it.
    """

    activate(DESKTOP_CAPABILITY)
    try:
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(inferbridge_url, wait_until="load")
        page.wait_for_function("() => Boolean(window.InferBridge)", timeout=15_000)
        page.wait_for_selector("#storage-manager-btn", timeout=15_000)
        page.wait_for_timeout(500)
        assert [text for text in errors if "InferBridge" in text] == [], errors
        yield page
    finally:
        deactivate(DESKTOP_CAPABILITY)


def test_desktop_surfaces_are_absent_without_the_capability(page: Page, inferbridge_url: str):
    page.goto(inferbridge_url, wait_until="load")
    page.wait_for_function("() => Boolean(window.InferBridge)", timeout=15_000)
    expect(page.locator("#storage-manager-btn")).to_have_count(0)
    expect(page.locator("#runtime-health-modal")).to_have_count(0)


def test_desktop_surfaces_compose_into_the_header_when_activated(desktop_page: Page):
    expect(desktop_page.locator("#storage-manager-btn")).to_have_count(1)
    expect(desktop_page.locator("#storage-manager-modal")).to_have_count(1)
    expect(desktop_page.locator("#runtime-health-modal")).to_have_count(1)
    # Runtime health installs an entry point into both surfaces it decorates: the storage
    # manager and the model library. Both must be composed for either to appear.
    expect(desktop_page.locator("[data-open-runtime-health]")).to_have_count(2)


def test_storage_manager_opens_and_reports_missing_routes_honestly(desktop_page: Page):
    """A surface without its backend must say so, not fail silently or claim success."""

    desktop_page.locator("#storage-manager-btn").click()
    modal = desktop_page.locator("#storage-manager-modal")
    expect(modal).not_to_have_class("hidden")
    expect(modal).to_have_attribute("aria-hidden", "false")
    expect(modal.locator("#sm-content")).to_contain_text(
        "Storage inventory is unavailable", timeout=10_000
    )
    # No reclaimable totals are invented while the inventory is unknown.
    expect(modal.locator("#sm-summary")).to_be_empty()


def test_storage_manager_closes_and_returns_focus(desktop_page: Page):
    trigger = desktop_page.locator("#storage-manager-btn")
    trigger.click()
    modal = desktop_page.locator("#storage-manager-modal")
    expect(modal).not_to_have_class("hidden")

    desktop_page.locator("#sm-close").click()
    expect(modal).to_have_class("modal-overlay hidden")
    expect(modal).to_have_attribute("aria-hidden", "true")
    assert desktop_page.evaluate("() => document.activeElement?.id") == "storage-manager-btn"


def test_desktop_surfaces_register_into_the_shared_request_stack(desktop_page: Page):
    """Gated surfaces must not reintroduce a private window.fetch wrapper."""

    stack = desktop_page.evaluate("() => window.InferBridge.middleware()")
    assert stack[0] == "inferBridgeTelemetry", stack
    assert len(stack) == len(set(stack)), stack
