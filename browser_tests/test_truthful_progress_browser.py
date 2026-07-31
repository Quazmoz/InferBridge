from __future__ import annotations

import copy
import json
from typing import Any
from urllib.request import urlopen

from playwright.sync_api import Page, Route, expect


def _server_snapshot(inferbridge_url: str) -> dict[str, Any]:
    with urlopen(f"{inferbridge_url}/v1/models/status", timeout=10) as response:
        return json.load(response)


def _active_model(
    model: dict[str, Any],
    *,
    percent: float | None,
    completed: int | None = None,
    total: int | None = None,
) -> dict[str, Any]:
    updated = dict(model)
    updated.update(
        {
            "status": "converting",
            "status_label": "Converting model to OpenVINO IR…",
            "is_loaded": False,
            "is_loading": True,
            "progress": {
                "schema_version": 1,
                "operation_id": "convert-truthful-progress",
                "operation_type": "convert",
                "revision": 4,
                "phase": "converting",
                "message": "Converting model to OpenVINO IR…",
                "percent": percent,
                "completed": completed,
                "total": total,
                "started_at": 1_900,
                "updated_at": 2_000,
                "log_tail": ["Untrusted human log says 99%"],
            },
        }
    )
    return updated


def _route_model_status(
    page: Page,
    inferbridge_url: str,
    *,
    percent: float | None,
    completed: int | None = None,
    total: int | None = None,
) -> None:
    baseline = _server_snapshot(inferbridge_url)

    def handle_status(route: Route) -> None:
        payload = copy.deepcopy(baseline)
        models = list(payload["models"]["available"])
        models[0] = _active_model(
            dict(models[0]),
            percent=percent,
            completed=completed,
            total=total,
        )
        payload["models"] = {
            **payload["models"],
            "available": models,
            "loading_count": 1,
        }
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

    page.route("**/v1/models/status", handle_status)


def test_phase_percent_is_not_reweighted_as_overall_progress(
    page: Page,
    inferbridge_url: str,
) -> None:
    _route_model_status(page, inferbridge_url, percent=40)
    page.goto(inferbridge_url, wait_until="domcontentloaded")

    dock = page.locator("#ov-reliable-progress")
    expect(dock).to_be_visible(timeout=15_000)
    expect(dock.locator(".ovrp-value")).to_have_text("40%")
    expect(dock.locator(".ovrp-track")).to_have_attribute("aria-valuenow", "40")

    dock.locator(".ovrp-main").click()
    expect(dock.locator(".ovrp-meta")).to_contain_text("40% of current phase")
    expect(dock.locator(".ovrp-meta")).not_to_contain_text("overall")


def test_completed_counts_drive_phase_progress_when_available(
    page: Page,
    inferbridge_url: str,
) -> None:
    _route_model_status(page, inferbridge_url, percent=95, completed=8, total=14)
    page.goto(inferbridge_url, wait_until="domcontentloaded")

    dock = page.locator("#ov-reliable-progress")
    expect(dock).to_be_visible(timeout=15_000)
    expect(dock.locator(".ovrp-value")).to_have_text("8 of 14")
    expect(dock.locator(".ovrp-track")).to_have_attribute("aria-valuenow", "57")

    dock.locator(".ovrp-main").click()
    expect(dock.locator(".ovrp-meta")).to_contain_text("8 of 14")
    expect(dock.locator(".ovrp-meta")).to_contain_text("57% of current phase")
