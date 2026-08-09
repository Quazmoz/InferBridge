"""Runtime upgrade and model health maintenance regression tests."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import runtime_health
from app.runtime_health import (
    RuntimeHealthActionRequest,
    RuntimeHealthBatchRequest,
    RuntimeHealthService,
    maintenance_recommendation,
)
from app.storage_safety import StorageConflict


class FakeConfig:
    def __init__(self, model_id: str, root: Path) -> None:
        self.id = model_id
        self.name = f"Model {model_id}"
        self.source_model = f"org/{model_id}"
        self.backend = "openvino-genai"
        self.weight_format = "int4"
        self.recommended_device = "CPU"
        self._root = root

    def abs_path(self, _base_dir: Path) -> Path:
        return self._root / self.id


class FakeEngine:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeManager:
    def __init__(self, catalog: dict[str, FakeConfig]) -> None:
        self.catalog = catalog
        self.force_mock = True
        self.engines: dict[str, object] = {}
        self.load_tasks: dict[str, object] = {}
        self.convert_tasks: dict[str, object] = {}
        self.status_overrides: dict[str, dict] = {}
        self._model_recovery_locks: dict[str, object] = {}
        self.events: list[tuple[str, str]] = []
        self.engines_built: list[tuple[str, str, FakeEngine]] = []
        self.convert_calls: list[tuple[str, str, dict]] = []

    async def build_temporary_engine(self, model_id: str, device: str):
        engine = FakeEngine()
        self.engines_built.append((model_id, device, engine))
        return engine, 0.012

    def emit_event(self, level: str, message: str) -> None:
        self.events.append((level, message))

    def schedule_convert(self, model_id: str, device: str, **kwargs):
        self.convert_calls.append((model_id, device, kwargs))
        return object()


class FakeStorage:
    def __init__(self) -> None:
        self.cleanup_calls = []

    async def cleanup(self, request):
        self.cleanup_calls.append(request)
        return {"status": "completed", "freed_bytes": 1234}


def make_service(tmp_path: Path, model_ids: tuple[str, ...] = ("model-a",)):
    catalog = {model_id: FakeConfig(model_id, tmp_path) for model_id in model_ids}
    manager = FakeManager(catalog)
    storage = FakeStorage()
    service = RuntimeHealthService(
        settings=SimpleNamespace(device="CPU"),
        manager=manager,
        paths=SimpleNamespace(config_dir=tmp_path / "config"),
        storage=storage,
    )
    service._conversion_fingerprint = lambda cfg: f"fingerprint-{cfg.id}"
    service._source_cache_state = lambda _cfg: "reusable"
    return service, manager, storage


def test_maintenance_policy_maps_existing_health_states_to_safe_actions():
    assert maintenance_recommendation("compatible")["action"] == "leave_unchanged"
    assert maintenance_recommendation("legacy_untracked")["action"] == "revalidate"
    assert maintenance_recommendation("stale_runtime")["action"] == "rebuild_compiled_cache"

    damaged = maintenance_recommendation(
        "invalid_metadata",
        source_cache_reusable=True,
    )
    assert damaged["action"] == "reconvert"
    assert damaged["label"] == "Reconvert from existing HF cache"
    assert damaged["safe_batch"] is False

    failed = maintenance_recommendation(
        "stale_runtime",
        validation_failed=True,
        source_cache_reusable=True,
    )
    assert failed["action"] == "reconvert"
    assert failed["label"] == "Reconvert from existing HF cache"

    validated = maintenance_recommendation("stale_runtime", validation_current=True)
    assert validated["action"] == "leave_unchanged"
    assert "Validated" in validated["label"]

    acknowledged = maintenance_recommendation("legacy_untracked", acknowledged_current=True)
    assert acknowledged["action"] == "leave_unchanged"


def test_batch_request_deduplicates_models_and_rejects_unsafe_ids():
    request = RuntimeHealthBatchRequest(
        action="revalidate",
        model_ids=["model-a", "model-a", "model-b"],
    )
    assert request.model_ids == ["model-a", "model-b"]

    with pytest.raises(ValueError):
        RuntimeHealthBatchRequest(action="revalidate", model_ids=["../outside"])

    with pytest.raises(ValueError):
        RuntimeHealthBatchRequest(action="reconvert", model_ids=["model-a"])


def test_runtime_evidence_survives_app_patch_when_openvino_is_unchanged(tmp_path):
    service, _manager, _storage = make_service(tmp_path)
    record = {
        "runtime": {
            "application": "1.0.0",
            "openvino": "2026.1.0",
            "openvino_genai": "2026.1.0",
        }
    }
    current = {
        "application": "1.0.1",
        "openvino": "2026.1.0",
        "openvino_genai": "2026.1.0",
    }
    assert service._runtime_matches(record, current) is True

    current["openvino_genai"] = "2026.2.0"
    assert service._runtime_matches(record, current) is False


def test_batch_revalidate_loads_sequentially_and_records_current_runtime(tmp_path, monkeypatch):
    service, manager, storage = make_service(tmp_path, ("model-a", "model-b"))
    monkeypatch.setattr(
        runtime_health,
        "current_runtime_versions",
        lambda: {
            "application": "1.2.3",
            "openvino": "2026.2.0",
            "openvino_genai": "2026.2.0",
        },
    )
    monkeypatch.setattr(
        runtime_health,
        "conversion_health",
        lambda _cfg: {"status": "legacy_untracked", "label": "Legacy", "details": ""},
    )

    result = asyncio.run(
        service.perform_batch(
            RuntimeHealthBatchRequest(
                action="revalidate",
                model_ids=["model-a", "model-b"],
            )
        )
    )

    assert result["status"] == "completed"
    assert [item["model_id"] for item in result["validated"]] == ["model-a", "model-b"]
    assert storage.cleanup_calls == []
    assert len(manager.engines_built) == 2
    assert all(engine.closed for _, _, engine in manager.engines_built)

    state = json.loads(service.state_file.read_text(encoding="utf-8"))
    for model_id in ("model-a", "model-b"):
        validation = state["models"][model_id]["validation"]
        assert validation["status"] == "passed"
        assert validation["conversion_fingerprint"] == f"fingerprint-{model_id}"
        assert validation["runtime"]["openvino"] == "2026.2.0"


def test_batch_cache_rebuild_clears_shared_cache_once_then_warms_selected_models(
    tmp_path, monkeypatch
):
    service, manager, storage = make_service(tmp_path, ("model-a", "model-b"))
    monkeypatch.setattr(
        runtime_health,
        "conversion_health",
        lambda _cfg: {"status": "stale_runtime", "label": "Runtime changed", "details": ""},
    )

    result = asyncio.run(
        service.perform_batch(
            RuntimeHealthBatchRequest(
                action="rebuild_compiled_cache",
                model_ids=["model-a", "model-b"],
            )
        )
    )

    assert result["status"] == "completed"
    assert result["freed_bytes"] == 1234
    assert len(storage.cleanup_calls) == 1
    assert storage.cleanup_calls[0].action == "clear_compiled_cache"
    assert [item["model_id"] for item in result["validated"]] == ["model-a", "model-b"]
    assert all(engine.closed for _, _, engine in manager.engines_built)


def test_reconvert_reuses_recorded_quantization_profile_and_does_not_batch(tmp_path, monkeypatch):
    service, manager, _storage = make_service(tmp_path)
    monkeypatch.setattr(
        runtime_health.model_load_safety,
        "_read_profile",
        lambda _cfg, _base: {
            "group_size": 128,
            "ratio": 1.0,
            "symmetric": True,
        },
    )

    result = asyncio.run(
        service.perform(
            RuntimeHealthActionRequest(
                action="reconvert",
                model_id="model-a",
            )
        )
    )

    assert result == {
        "status": "scheduled",
        "action": "reconvert",
        "model_id": "model-a",
        "source_cache": "reusable",
    }
    assert len(manager.convert_calls) == 1
    model_id, device, kwargs = manager.convert_calls[0]
    assert (model_id, device) == ("model-a", "CPU")
    assert kwargs == {
        "load_after": False,
        "weight_format": "int4",
        "group_size": 128,
        "ratio": 1.0,
        "sym": True,
    }


def test_validation_rejects_definition_mismatch_without_loading(tmp_path, monkeypatch):
    service, manager, _storage = make_service(tmp_path)
    monkeypatch.setattr(
        runtime_health,
        "conversion_health",
        lambda _cfg: {
            "status": "incompatible_definition",
            "label": "Definition changed",
            "details": "",
        },
    )

    with pytest.raises(StorageConflict) as exc_info:
        asyncio.run(service._validate_one("model-a", None))

    assert exc_info.value.code == "reconversion_required"
    assert manager.engines_built == []
