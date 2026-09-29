from __future__ import annotations

import json

from playwright.sync_api import Page, expect


def _library_payload() -> dict:
    return {
        "schema_version": 1,
        "profile": "balanced",
        "manifest": {"source": "bundled", "generated_at": "", "catalog_sha256": ""},
        "profiles": ["fastest", "balanced", "best_quality", "lowest_memory"],
        "items": [],
        "count": 0,
        "include_all": False,
        "caveat": "",
    }


def _status_payload(*, adopted: bool) -> dict:
    models = []
    if adopted:
        models.append(
            {
                "id": "qwen-local-64k",
                "name": "Qwen Local 64K",
                "description": "Imported local model",
                "status": "ready_to_load",
                "status_label": "Converted and ready",
                "is_loaded": False,
                "is_loading": False,
                "can_load": True,
                "can_convert": False,
                "can_unload": False,
                "can_delete": True,
                "device": None,
            }
        )
    return {
        "schema_version": 1,
        "generated_at": 0,
        "memory": {"used_percent": 10},
        "disk": {"models_gb": 0.0, "free_gb": 100.0, "total_gb": 128.0},
        "device": {
            "default": "CPU",
            "mock": True,
            "available": ["CPU"],
            "busy": False,
            "loaded": {},
        },
        "models": {
            "loaded": [],
            "count": len(models),
            "loading_count": 0,
            "available": models,
        },
        "metrics": {"totals": {"requests": 0, "completion_tokens": 0, "avg_latency_ms": 0}},
        "events": [],
    }


def test_detected_local_model_can_be_registered_and_selected(
    page: Page,
    inferbridge_url: str,
) -> None:
    page.set_viewport_size({"width": 1600, "height": 900})
    page.add_init_script("localStorage.setItem('inferbridge.onboarding.auto-opened.v1', '1')")

    adopted = {"value": False}
    submitted: list[dict] = []

    def discovery(route) -> None:
        models = []
        if not adopted["value"]:
            models.append(
                {
                    "directory_name": "qwen-local-64k",
                    "suggested_model_id": "qwen-local-64k",
                    "suggested_name": "qwen local 64k",
                    "size_bytes": 123456,
                }
            )
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"models": models, "count": len(models)}),
        )

    def status(route) -> None:
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(_status_payload(adopted=adopted["value"])),
        )

    def adopt(route) -> None:
        body = json.loads(route.request.post_data or "{}")
        submitted.append(body)
        adopted["value"] = True
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "status": "imported",
                    "model_id": "qwen-local-64k",
                    "target_path": r"C:\\InferBridge\\models\\qwen-local-64k",
                    "size_bytes": 123456,
                    "managed_in_place": True,
                    "conversion_health": {
                        "status": "legacy_untracked",
                        "label": "Converted, compatibility unknown",
                        "details": "",
                    },
                    "model": {
                        "id": "qwen-local-64k",
                        "name": "Qwen Local 64K",
                    },
                }
            ),
        )

    page.route("**/v1/model-library/unregistered-managed*", discovery)
    page.route(
        "**/v1/model-library?*",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(_library_payload()),
        ),
    )
    page.route("**/v1/model-library/adopt-managed", adopt)
    page.route("**/v1/models/status", status)
    page.route("**/v1/system/status", status)

    page.goto(inferbridge_url, wait_until="networkidle")

    quick_import = page.locator("#import-local-model-btn")
    expect(quick_import).to_be_visible()
    expect(quick_import).to_have_attribute("data-count", "1")
    quick_import.click()

    modal = page.locator("#model-library-modal")
    expect(modal).to_be_visible()
    expect(page.locator("#ml-discovered")).to_be_visible()
    expect(page.locator("#ml-discovered-list")).to_contain_text("qwen-local-64k")

    page.get_by_role("button", name="Review & register").click()
    expect(page.locator("#ml-import-title")).to_have_text("Register detected local OpenVINO model")
    expect(page.locator("#ml-import-id")).to_have_value("qwen-local-64k")
    expect(page.locator("#ml-import-name")).to_have_value("qwen local 64k")
    expect(page.locator("#ml-import-path")).to_be_disabled()

    page.locator("#ml-import-name").fill("Qwen Local 64K")
    page.locator("#ml-import-format").select_option("int4")
    page.locator("#ml-import-context").fill("65536")
    page.get_by_role("button", name="Import and manage copy").click()

    expect(modal).to_be_hidden()
    expect(page.locator("#model-select")).to_have_value("qwen-local-64k")
    expect(page.locator("#toast")).to_contain_text(
        "Qwen Local 64K imported and selected — ready to load."
    )
    expect(quick_import).to_have_attribute("data-count", "0")

    assert submitted == [
        {
            "model_id": "qwen-local-64k",
            "name": "Qwen Local 64K",
            "backend": "openvino-genai",
            "weight_format": "int4",
            "recommended_device": "CPU",
            "max_context_len": 65536,
            "max_output_tokens": 512,
            "directory_name": "qwen-local-64k",
        }
    ]
