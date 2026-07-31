from __future__ import annotations

import copy
import json
import re
from typing import Any
from urllib.request import urlopen

from playwright.sync_api import Page, Route, expect


def _operation_snapshot(
    operation_id: str,
    revision: int,
    *,
    phase: str = "converting",
    percent: float | None = 40,
    updated_at: int = 2_000,
    started_at: int = 1_900,
    can_cancel: bool = True,
    cancel_mode: str | None = "conversion",
    cancel_reason: str | None = None,
) -> dict[str, Any]:
    active = phase not in {"ready", "cancelled", "error"}
    status = "converting" if active else phase
    return {
        "operation_id": operation_id,
        "revision": revision,
        "phase": phase,
        "percent": percent,
        "updated_at": updated_at,
        "started_at": started_at,
        "status": status,
        "is_loading": active,
        "can_cancel": can_cancel,
        "cancel_mode": cancel_mode,
        "cancel_reason": cancel_reason,
    }


def _server_snapshot(inferbridge_url: str) -> dict[str, Any]:
    with urlopen(f"{inferbridge_url}/v1/models/status", timeout=10) as response:
        return json.load(response)


def _apply_snapshot(model: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    updated = dict(model)
    progress = dict(updated.get("progress") or {})
    progress.update(
        {
            "schema_version": 1,
            "operation_id": snapshot["operation_id"],
            "operation_type": "convert",
            "revision": snapshot["revision"],
            "phase": snapshot["phase"],
            "message": (
                "Conversion cancelled."
                if snapshot["phase"] == "cancelled"
                else f"Converting model to OpenVINO IR at {snapshot['percent']}%."
            ),
            "percent": snapshot["percent"],
            "completed": None,
            "total": None,
            "started_at": snapshot["started_at"],
            "updated_at": snapshot["updated_at"],
            "log_tail": ["Browser-controlled progress event"],
        }
    )
    updated.update(
        {
            "status": snapshot["status"],
            "status_label": progress["message"],
            "is_loaded": False,
            "is_loading": snapshot["is_loading"],
            "can_cancel": snapshot["can_cancel"],
            "cancel_mode": snapshot["cancel_mode"],
            "cancel_reason": snapshot["cancel_reason"],
            "progress": progress,
        }
    )
    return updated


def _install_status_controller(page: Page, state: dict[str, Any]) -> None:
    baseline = _server_snapshot(state["inferbridge_url"])

    def handle_status(route: Route) -> None:
        if state.pop("abort_once", False):
            route.abort()
            return

        payload = copy.deepcopy(baseline)
        available = list(payload["models"]["available"])
        model = _apply_snapshot(dict(available[0]), state["snapshot"])
        available[0] = model
        payload["models"] = {
            **payload["models"],
            "available": available,
            "loading_count": 1 if state["snapshot"]["is_loading"] else 0,
        }
        state["model_id"] = model["id"]
        state["last_model"] = model
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(payload),
        )

    page.route("**/v1/models/status", handle_status)


def _open_progress(page: Page, inferbridge_url: str, state: dict[str, Any]) -> None:
    state["inferbridge_url"] = inferbridge_url
    _install_status_controller(page, state)
    page.goto(inferbridge_url, wait_until="domcontentloaded")
    expect(page.locator("#ov-reliable-progress")).to_have_attribute(
        "data-operation-id",
        state["snapshot"]["operation_id"],
        timeout=15_000,
    )
    model_id = state.get("model_id")
    if model_id:
        page.locator("#model-select").select_option(model_id)


def _invalidate_and_fetch_status(page: Page) -> None:
    page.evaluate(
        """
        () => {
            window.__inferbridgeInvalidateModelStatus?.();
            return fetch('/v1/system/status', {cache: 'no-store'}).then(response => response.json());
        }
        """
    )


def test_lower_server_revision_cannot_regress_visible_operation(
    page: Page,
    inferbridge_url: str,
) -> None:
    state = {"snapshot": _operation_snapshot("convert-browser-stable", 8, percent=82)}
    _open_progress(page, inferbridge_url, state)

    dock = page.locator("#ov-reliable-progress")
    expect(dock).to_have_attribute("data-operation-revision", "8")

    state["snapshot"] = _operation_snapshot(
        "convert-browser-stable",
        7,
        percent=9,
        updated_at=1_999,
    )
    _invalidate_and_fetch_status(page)
    page.wait_for_timeout(250)

    expect(dock).to_have_attribute("data-operation-revision", "8")
    expect(dock.locator(".ovrp-message")).to_contain_text("82%")


def test_cancel_button_posts_exact_operation_and_reconciles_terminal_state(
    page: Page,
    inferbridge_url: str,
) -> None:
    operation_id = "convert-browser-cancel"
    state = {"snapshot": _operation_snapshot(operation_id, 4, percent=51)}
    requests: list[dict[str, Any]] = []

    def handle_cancel(route: Route) -> None:
        requests.append(route.request.post_data_json)
        state["snapshot"] = _operation_snapshot(
            operation_id,
            5,
            phase="cancelled",
            percent=51,
            updated_at=2_001,
            can_cancel=False,
            cancel_mode=None,
            cancel_reason="The model preparation operation has already finished.",
        )
        model = _apply_snapshot(dict(state["last_model"]), state["snapshot"])
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "status": "cancelled",
                    "operation_id": operation_id,
                    "cancel_mode": "conversion",
                    "already_cancelled": False,
                    "message": "Cancelled model preparation for the selected model.",
                    "model": model,
                }
            ),
        )

    page.route("**/v1/models/cancel", handle_cancel)
    page.on("dialog", lambda dialog: dialog.accept())
    _open_progress(page, inferbridge_url, state)

    dock = page.locator("#ov-reliable-progress")
    dock.locator(".ovrp-main").click()
    button = page.get_by_role("button", name=re.compile("Cancel conversion for"))
    expect(button).to_be_visible()
    button.click()

    expect(dock).to_have_class(re.compile(r"\bcancelled\b"), timeout=10_000)
    expect(page.locator(".ovrp-cancel-button")).to_have_count(0)
    assert requests == [{"model": state["model_id"], "operation_id": operation_id}]


def test_stale_cancel_conflict_refreshes_to_new_operation(
    page: Page,
    inferbridge_url: str,
) -> None:
    old_operation = "convert-browser-old"
    new_operation = "convert-browser-new"
    state = {"snapshot": _operation_snapshot(old_operation, 12, percent=60)}
    requests: list[dict[str, Any]] = []

    def handle_cancel(route: Route) -> None:
        requests.append(route.request.post_data_json)
        state["snapshot"] = _operation_snapshot(
            new_operation,
            13,
            percent=5,
            updated_at=2_100,
            started_at=2_090,
        )
        route.fulfill(
            status=409,
            content_type="application/json",
            body=json.dumps(
                {
                    "detail": {
                        "code": "stale_operation",
                        "message": "The requested operation is no longer current.",
                        "current_operation_id": new_operation,
                    }
                }
            ),
        )

    page.route("**/v1/models/cancel", handle_cancel)
    page.on("dialog", lambda dialog: dialog.accept())
    _open_progress(page, inferbridge_url, state)

    page.locator("#ov-reliable-progress .ovrp-main").click()
    page.get_by_role("button", name=re.compile("Cancel conversion for")).click()

    expect(page.locator("#ov-reliable-progress")).to_have_attribute(
        "data-operation-id",
        new_operation,
        timeout=10_000,
    )
    assert requests == [{"model": state["model_id"], "operation_id": old_operation}]


def test_failed_status_request_allows_lower_revision_after_server_restart(
    page: Page,
    inferbridge_url: str,
) -> None:
    state = {
        "snapshot": _operation_snapshot(
            "convert-before-restart",
            30,
            percent=70,
            updated_at=3_000,
            started_at=2_900,
        )
    }
    _open_progress(page, inferbridge_url, state)

    # Abort both the split endpoint and its compatibility fallback so the operation
    # reconciler observes a genuine connectivity failure and clears its watermark.
    fallback_aborted = {"value": False}

    def abort_legacy_once(route: Route) -> None:
        if not fallback_aborted["value"]:
            fallback_aborted["value"] = True
            route.abort()
        else:
            route.continue_()

    page.route("**/v1/system/status", abort_legacy_once)
    state["abort_once"] = True
    page.evaluate(
        """
        () => {
            window.__inferbridgeInvalidateModelStatus?.();
            return fetch('/v1/system/status', {cache: 'no-store'}).catch(() => null);
        }
        """
    )
    state["snapshot"] = _operation_snapshot(
        "convert-after-restart",
        1,
        percent=3,
        updated_at=4_000,
        started_at=3_990,
    )
    _invalidate_and_fetch_status(page)

    expect(page.locator("#ov-reliable-progress")).to_have_attribute(
        "data-operation-id",
        "convert-after-restart",
        timeout=10_000,
    )
    expect(page.locator("#ov-reliable-progress")).to_have_attribute(
        "data-operation-revision",
        "1",
    )


def test_mobile_keyboard_and_reduced_motion_behavior(
    page: Page,
    inferbridge_url: str,
) -> None:
    page.set_viewport_size({"width": 390, "height": 844})
    page.emulate_media(reduced_motion="reduce")
    state = {"snapshot": _operation_snapshot("convert-mobile", 2, percent=25)}
    _open_progress(page, inferbridge_url, state)

    dock = page.locator("#ov-reliable-progress")
    box = dock.bounding_box()
    assert box is not None
    assert box["x"] >= 0
    assert box["x"] + box["width"] <= 390

    main = dock.locator(".ovrp-main")
    main.focus()
    page.keyboard.press("Enter")
    expect(dock).to_have_class(re.compile(r"\bexpanded\b"))

    cancel = page.get_by_role("button", name=re.compile("Cancel conversion for"))
    expect(cancel).to_be_visible()
    cancel_box = cancel.bounding_box()
    assert cancel_box is not None
    assert cancel_box["x"] >= 0
    assert cancel_box["x"] + cancel_box["width"] <= 390

    animation_name = dock.locator(".ovrp-spinner").evaluate(
        "element => getComputedStyle(element).animationName"
    )
    assert animation_name == "none"


def test_expandable_queue_lists_all_active_operations_and_changes_primary(
    page: Page,
    inferbridge_url: str,
) -> None:
    baseline = _server_snapshot(inferbridge_url)
    available = baseline["models"]["available"]
    assert len(available) >= 3

    snapshots = [
        _operation_snapshot("convert-queue-1", 4, phase="converting", percent=72),
        _operation_snapshot("load-queue-2", 2, phase="queued", percent=None),
        _operation_snapshot("convert-queue-3", 7, phase="downloading", percent=41),
    ]
    model_ids = [available[index]["id"] for index in range(3)]

    def handle_status(route: Route) -> None:
        payload = copy.deepcopy(baseline)
        models = list(payload["models"]["available"])
        for index, snapshot in enumerate(snapshots):
            models[index] = _apply_snapshot(dict(models[index]), snapshot)
        payload["models"] = {
            **payload["models"],
            "available": models,
            "loading_count": 3,
        }
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

    page.route("**/v1/models/status", handle_status)
    page.goto(inferbridge_url, wait_until="domcontentloaded")

    dock = page.locator("#ov-reliable-progress")
    expect(dock).to_have_attribute("data-operation-id", "convert-queue-1", timeout=15_000)
    dock.locator(".ovrp-main").click()

    toggle = page.locator(".ovrp-queue-toggle")
    expect(toggle).to_have_text("3 operations active")
    toggle.click()

    panel = page.locator("#ovrp-operation-queue")
    expect(panel).to_be_visible()
    rows = panel.locator(".ovrp-queue-row")
    expect(rows).to_have_count(3)
    expect(rows.nth(0)).to_contain_text("72%")
    expect(rows.nth(1)).to_contain_text("Waiting to start")
    expect(rows.nth(2)).to_contain_text("Downloading")
    expect(rows.nth(2)).to_contain_text("41%")

    rows.nth(2).click()
    expect(page.locator("#model-select")).to_have_value(model_ids[2])
    expect(dock).to_have_attribute("data-operation-id", "convert-queue-3", timeout=10_000)
    expect(panel).to_be_visible()
