from __future__ import annotations

import json

from playwright.sync_api import Page, expect

RECOVERY_ID = "recovery-browser-test"
MODEL_ID = "qwen2.5-3b-instruct-int4"


def _recovery_payload(*, include_details: bool = False) -> dict:
    recovery = {
        "available": True,
        "schema_version": 1,
        "recovery_id": RECOVERY_ID,
        "model_id": MODEL_ID,
        "model_name": "Qwen 2.5 3B",
        "interrupted_at": 1785668400,
        "terminal_state": "error",
        "downloaded_files": "reusable",
        "conversion_output": "incomplete",
        "failed_stage": "conversion",
        "last_completed_stage": "download",
        "recommended_action": "resume",
        "actions": {
            "resume": True,
            "retry_failed_stage": True,
            "restart_download": True,
            "remove_incomplete_files": True,
            "view_failure_details": True,
        },
    }
    if include_details:
        recovery["failure_details"] = {
            "message": "Conversion exited before OpenVINO files were complete.",
            "log_tail": [
                "Downloading model files complete.",
                "Converting model to OpenVINO IR.",
            ],
        }
    return recovery


def _status_payload() -> dict:
    return {
        "schema_version": 1,
        "generated_at": 1785668400,
        "device": {
            "default": "CPU",
            "mock": True,
            "loaded": {},
            "busy": False,
        },
        "models": {
            "loaded": [],
            "count": 0,
            "loading_count": 0,
            "available": [
                {
                    "id": MODEL_ID,
                    "name": "Qwen 2.5 3B",
                    "status": "error",
                    "is_loading": False,
                    "recovery": _recovery_payload(),
                }
            ],
        },
    }


def test_recovery_screen_shows_state_details_and_starts_resume(
    page: Page,
    inferbridge_url: str,
) -> None:
    page.add_init_script("localStorage.setItem('inferbridge.onboarding.auto-opened.v1', '1')")
    page.goto(inferbridge_url, wait_until="networkidle")

    submitted_actions = []
    page.route(
        "**/v1/models/status",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(_status_payload()),
        ),
    )
    page.route(
        f"**/v1/models/recovery/{MODEL_ID}",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(_recovery_payload(include_details=True)),
        ),
    )

    def handle_action(route) -> None:
        submitted_actions.append(json.loads(route.request.post_data or "{}"))
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "status": "started",
                    "action": "resume",
                    "started_action": "resume_conversion",
                    "message": "Recovery started for Qwen 2.5 3B.",
                }
            ),
        )

    page.route("**/v1/models/recovery/action", handle_action)
    page.evaluate("fetch('/v1/models/status').then(response => response.json())")

    overlay = page.locator("#ov-model-recovery-overlay")
    expect(overlay).to_be_visible()
    expect(overlay.locator("#ovmr-title")).to_have_text("Qwen 2.5 3B preparation was interrupted")
    expect(overlay).to_contain_text("Downloaded files")
    expect(overlay).to_contain_text("Reusable")
    expect(overlay).to_contain_text("Conversion output")
    expect(overlay).to_contain_text("Incomplete")
    expect(overlay).to_contain_text("Last completed stage")
    expect(overlay).to_contain_text("Download")
    expect(overlay).to_contain_text("Recommended action")

    overlay.get_by_role("button", name="View sanitized failure details").click()
    expect(overlay.locator(".ovmr-details")).to_be_visible()
    expect(overlay.locator(".ovmr-details-message")).to_have_text(
        "Conversion exited before OpenVINO files were complete."
    )
    expect(overlay.locator(".ovmr-details-log")).to_contain_text("Converting model to OpenVINO IR.")

    overlay.get_by_role("button", name="Resume preparation").click()
    expect(overlay).to_be_hidden()
    assert submitted_actions == [
        {
            "model": MODEL_ID,
            "recovery_id": RECOVERY_ID,
            "action": "resume",
            "device": "CPU",
        }
    ]
