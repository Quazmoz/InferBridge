from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

from app import model_registry as registry
from app.huggingface_manager_safety import install_huggingface_manager_safety
from app.model_manager import ModelManager


def _catalog_entry(tmp_path, *, source_model: str = "publisher/gated-model"):
    model_path = tmp_path / "converted"
    catalog_path = tmp_path / "models.json"
    catalog_path.write_text(
        json.dumps(
            {
                "gated": {
                    "name": "Gated Model",
                    "description": "Test model",
                    "backend": "openvino-genai",
                    "model_path": str(model_path),
                    "source_model": source_model,
                    "access_type": "gated",
                    "model_url": f"https://huggingface.co/{source_model}",
                    "license_url": f"https://huggingface.co/{source_model}",
                    "weight_format": "fp16",
                    "recommended_device": "CPU",
                    "max_context_len": 2048,
                    "max_output_tokens": 512,
                }
            }
        ),
        encoding="utf-8",
    )
    return catalog_path, registry.load_catalog(catalog_path)["gated"]


def test_catalog_rewrites_preserve_matching_huggingface_access_metadata(tmp_path):
    catalog_path, cfg = _catalog_entry(tmp_path)
    install_huggingface_manager_safety()

    registry.save_catalog(catalog_path, {"gated": cfg})

    saved = json.loads(catalog_path.read_text(encoding="utf-8"))["gated"]
    assert saved["access_type"] == "gated"
    assert saved["model_url"] == "https://huggingface.co/publisher/gated-model"
    assert saved["license_url"] == "https://huggingface.co/publisher/gated-model"
    assert not (tmp_path / ".models.json.hf-stage").exists()


def test_catalog_rewrites_drop_stale_access_metadata_after_source_change(tmp_path):
    catalog_path, cfg = _catalog_entry(tmp_path)
    install_huggingface_manager_safety()
    changed = replace(cfg, source_model="publisher/replacement-model")

    registry.save_catalog(catalog_path, {"gated": changed})

    saved = json.loads(catalog_path.read_text(encoding="utf-8"))["gated"]
    assert saved["source_model"] == "publisher/replacement-model"
    assert "access_type" not in saved
    assert "model_url" not in saved
    assert "license_url" not in saved


def test_internal_conversion_is_blocked_before_converter_start(tmp_path):
    _catalog_path, cfg = _catalog_entry(tmp_path)
    install_huggingface_manager_safety()
    calls = []
    statuses = []
    progress = []
    events = []

    class BlockedService:
        async def preflight(self, source_model, *, access_type):
            calls.append((source_model, access_type))
            return {
                "code": "hf_approval_required",
                "message": "Publisher approval is required.",
                "recoverable": True,
            }

    fake_manager = SimpleNamespace(
        catalog={"gated": cfg},
        force_mock=False,
        _hf_internal_access_service=BlockedService(),
        _hf_credential_store=object(),
        convert_tasks={"gated": object()},
        catalog_entry=lambda _model_id: {
            "huggingface_access": {"access_type": "gated"}
        },
        _set_status=lambda *args, **kwargs: statuses.append((args, kwargs)),
        _set_progress=lambda *args, **kwargs: progress.append((args, kwargs)),
        emit_event=lambda *args, **kwargs: events.append((args, kwargs)),
    )

    result = asyncio.run(
        ModelManager._convert_task(fake_manager, "gated", "CPU", False)
    )

    assert result is None
    assert calls == [("publisher/gated-model", "gated")]
    assert statuses[-1][0][:2] == ("gated", "error")
    assert "Publisher approval is required" in progress[-1][0][2]
    assert events[-1][0][0] == "warning"
    assert "gated" not in fake_manager.convert_tasks
