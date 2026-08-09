"""Aggregate runtime-health classification tests."""

from pathlib import Path
from types import SimpleNamespace

from app import runtime_health
from app.runtime_health import RuntimeHealthService


class SummaryConfig:
    def __init__(self, model_id: str, root: Path) -> None:
        self.id = model_id
        self.name = model_id.replace("-", " ").title()
        self.source_model = f"org/{model_id}"
        self.backend = "openvino-genai"
        self.weight_format = "int4"
        self.recommended_device = "CPU"
        self._root = root

    def abs_path(self, _base_dir: Path) -> Path:
        return self._root / self.id


class SummaryManager:
    def __init__(self, catalog) -> None:
        self.catalog = catalog
        self.force_mock = True
        self.engines = {}
        self.load_tasks = {}
        self.convert_tasks = {}
        self.status_overrides = {}
        self._model_recovery_locks = {}


class SummaryStorage:
    async def cleanup(self, _request):
        return {"status": "completed", "freed_bytes": 0}


def test_upgrade_summary_separates_revalidate_recompile_reconvert_and_unchanged(
    tmp_path, monkeypatch
):
    statuses = {
        "legacy-model": "legacy_untracked",
        "stale-a": "stale_runtime",
        "stale-b": "stale_runtime",
        "damaged-model": "invalid_metadata",
        "healthy-model": "compatible",
    }
    catalog = {model_id: SummaryConfig(model_id, tmp_path) for model_id in statuses}
    manager = SummaryManager(catalog)
    service = RuntimeHealthService(
        settings=SimpleNamespace(device="CPU"),
        manager=manager,
        paths=SimpleNamespace(config_dir=tmp_path / "config"),
        storage=SummaryStorage(),
    )
    service._conversion_fingerprint = lambda cfg: f"fingerprint-{cfg.id}"
    service._source_cache_state = lambda _cfg: "reusable"
    service._recorded_runtime = lambda _cfg: {}

    monkeypatch.setattr(
        runtime_health,
        "conversion_health",
        lambda cfg: {
            "status": statuses[cfg.id],
            "label": statuses[cfg.id].replace("_", " ").title(),
            "details": "",
        },
    )
    monkeypatch.setattr(
        runtime_health,
        "current_runtime_versions",
        lambda: {
            "application": "1.0.0",
            "openvino": "2026.2.0",
            "openvino_genai": "2026.2.0",
        },
    )

    snapshot = service._snapshot_sync()
    summary = snapshot["summary"]

    assert summary["models"] == 5
    assert summary["needs_attention"] == 4
    assert summary["revalidate"] == 1
    assert summary["rebuild_compiled_cache"] == 2
    assert summary["reconvert"] == 1
    assert summary["leave_unchanged"] == 1
    assert summary["unresolved_runtime_changes"] == 2
    assert summary["runtime_change_detected"] is True

    actions = {
        item["model_id"]: item["recommendation"]["action"] for item in snapshot["models"]
    }
    assert actions == {
        "legacy-model": "revalidate",
        "stale-a": "rebuild_compiled_cache",
        "stale-b": "rebuild_compiled_cache",
        "damaged-model": "reconvert",
        "healthy-model": "leave_unchanged",
    }
