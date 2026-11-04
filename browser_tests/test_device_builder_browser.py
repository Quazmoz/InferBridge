from __future__ import annotations

import json

from playwright.sync_api import Page, expect


def test_combined_device_builder_orders_members_and_sends_engine_options(
    page: Page,
    inferbridge_url: str,
) -> None:
    page.goto(inferbridge_url, wait_until="networkidle")
    combine = page.locator("#device-combine")
    combine.locator("summary").click()
    modes = page.locator("#device-combine-modes .device-combine-mode")
    expect(modes).to_have_count(4)
    expect(modes.nth(0)).to_contain_text("AUTO:GPU,CPU")
    expect(modes.nth(1)).to_contain_text("AUTO cumulative throughput")
    expect(modes.nth(1)).to_contain_text("Experimental")

    # Keyboard-reachable ordering: move CPU above GPU, the engine re-evaluates the list.
    page.get_by_role("button", name="Move CPU up").click()
    expect(modes.nth(2)).to_contain_text("HETERO:CPU,GPU")
    expect(page.get_by_role("button", name="Move CPU up")).to_be_disabled()

    # Adding the NPU: the engine (not the browser) marks NPU-in-HETERO unavailable.
    page.get_by_role("checkbox", name="Use NPU").check()
    expect(modes.nth(2)).to_contain_text("HETERO:CPU,GPU,NPU")
    expect(modes.nth(2).locator("input")).to_be_disabled()
    expect(modes.nth(2)).to_contain_text("Not available")
    page.get_by_role("checkbox", name="Use NPU, position 3").uncheck()
    expect(modes.nth(1)).to_contain_text("AUTO:CPU,GPU")

    modes.nth(1).locator("input").check()
    expect(page.locator("#device-combine-status")).to_contain_text("CUMULATIVE_THROUGHPUT")
    page.locator("#device-combine-use").click()
    expect(page.locator("#device-select")).to_have_value("AUTO:CPU,GPU")
    stored = json.loads(page.evaluate("localStorage.getItem('ovllm.device-builder.v1')"))
    assert stored["performance_hint"] == "CUMULATIVE_THROUGHPUT"
    assert page.evaluate("modelLoadOptions('AUTO:CPU,GPU')") == {
        "performance_hint": "CUMULATIVE_THROUGHPUT"
    }
    assert page.evaluate("modelLoadOptions('GPU')") == {}
