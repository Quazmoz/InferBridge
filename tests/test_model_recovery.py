import asyncio
import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.model_manager import ModelManager
from app.server import create_app

MODEL_ID = "model-1"


def _settings(tmp_path: Path, *, api_key: str | None = None) -> Settings:
    models_dir = tmp_path / "models" / "openvino"
    catalog_file = tmp_path / "models.json"
    catalog_file.write_text(
        json.dumps(
            {
                MODEL_ID: {
                    "name": "Qwen 2.5 3B",
                    "model_path": str(models_dir / MODEL_ID),
                    "source_model": "org/model-1",
                    "weight_format": "int4",
                    "recommended_device": "CPU",
                }
            }
        ),
        encoding="utf-8",
    )
    return Settings(
        models_file=catalog_file,
        models_dir=models_dir,
        cache_dir=tmp_path / "cache",
        benchmark_results_file=tmp_path / "benchmarks.json",
        force_mock=True,
        device="CPU",
        api_key=api_key,
    )


def _manager(tmp_path: Path) -> ModelManager:
    return ModelManager(_settings(tmp_path))


def _prepare_interrupted_conversion(manager: ModelManager, tmp_path: Path) -> dict:
    model_dir = Path(manager.catalog[MODEL_ID].model_path)
    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / "partial.bin").write_bytes(b"partial")

    manager._set_status(MODEL_ID, "converting")
    manager._set_progress(MODEL_ID, "downloading", "Downloading model files", percent=100)
    manager._set_progress(
        MODEL_ID,
        "converting",
        "Converting model",
        percent=42,
        append_log="hf_secretvalue C:\\Users\\Quinn\\model\\partial.bin",
    )
    manager._set_status(MODEL_ID, "error", error="conversion failed")
    manager._set_progress(MODEL_ID, "error", "Conversion failed")
    recovery = manager.model_recovery(MODEL_ID, include_details=True)
    assert recovery is not None
    return recovery


def _prepare_cache(monkeypatch, tmp_path: Path) -> Path:
    hub = tmp_path / "huggingface" / "hub"
    cache = hub / "models--org--model-1"
    blobs = cache / "blobs"
    blobs.mkdir(parents=True)
    (blobs / "weights").write_bytes(b"cached")
    monkeypatch.setenv("HUGGINGFACE_HUB_CACHE", str(hub))
    return cache


def test_interrupted_conversion_records_recovery_state(monkeypatch, tmp_path) -> None:
    _prepare_cache(monkeypatch, tmp_path)
    manager = _manager(tmp_path)

    recovery = _prepare_interrupted_conversion(manager, tmp_path)

    assert recovery["model_name"] == "Qwen 2.5 3B"
    assert recovery["downloaded_files"] == "reusable"
    assert recovery["conversion_output"] == "incomplete"
    assert recovery["last_completed_stage"] == "download"
    assert recovery["failed_stage"] == "conversion"
    assert recovery["recommended_action"] == "resume"
    assert recovery["actions"]["remove_incomplete_files"] is True
    details = recovery["failure_details"]
    assert "hf_secretvalue" not in "\n".join(details["log_tail"])
    assert "C:\\Users\\Quinn" not in "\n".join(details["log_tail"])
    assert (manager.settings.models_dir / ".inferbridge-recovery" / f"{MODEL_ID}.json").is_file()


def test_resume_keeps_download_cache_and_restarts_conversion(monkeypatch, tmp_path) -> None:
    cache = _prepare_cache(monkeypatch, tmp_path)
    manager = _manager(tmp_path)
    recovery = _prepare_interrupted_conversion(manager, tmp_path)
    calls = []

    def schedule_convert(model_id, device=None, *, load_after=True, **_kwargs):
        calls.append((model_id, device, load_after))
        return object()

    manager.schedule_convert = schedule_convert

    result = asyncio.run(
        manager.recover_model(
            MODEL_ID,
            recovery["recovery_id"],
            "resume",
            device="CPU",
        )
    )

    assert result["status"] == "started"
    assert result["started_action"] == "resume_conversion"
    assert calls == [(MODEL_ID, "CPU", True)]
    assert cache.is_dir()
    assert not Path(manager.catalog[MODEL_ID].model_path).exists()
    assert manager.model_recovery(MODEL_ID) is None


def test_retry_failed_load_reuses_complete_output(monkeypatch, tmp_path) -> None:
    _prepare_cache(monkeypatch, tmp_path)
    manager = _manager(tmp_path)
    model_dir = Path(manager.catalog[MODEL_ID].model_path)
    model_dir.mkdir(parents=True)
    (model_dir / "openvino_model.xml").write_text("<xml/>", encoding="utf-8")

    manager._set_status(MODEL_ID, "loading")
    manager._set_progress(MODEL_ID, "loading", "Loading model")
    manager._set_status(MODEL_ID, "error", error="load failed")
    manager._set_progress(MODEL_ID, "error", "Load failed")
    recovery = manager.model_recovery(MODEL_ID)
    assert recovery is not None
    assert recovery["failed_stage"] == "load"
    assert recovery["conversion_output"] == "complete"
    assert recovery["recommended_action"] == "retry_failed_stage"

    calls = []

    def schedule_load(model_id, device=None):
        calls.append((model_id, device))
        return object()

    manager.schedule_load = schedule_load
    result = asyncio.run(
        manager.recover_model(
            MODEL_ID,
            recovery["recovery_id"],
            "retry_failed_stage",
            device="CPU",
        )
    )

    assert result["started_action"] == "retry_load"
    assert calls == [(MODEL_ID, "CPU")]
    assert (model_dir / "openvino_model.xml").is_file()


def test_restart_download_removes_cache_and_incomplete_output(monkeypatch, tmp_path) -> None:
    cache = _prepare_cache(monkeypatch, tmp_path)
    manager = _manager(tmp_path)
    recovery = _prepare_interrupted_conversion(manager, tmp_path)
    calls = []

    def schedule_convert(model_id, device=None, *, load_after=True, **_kwargs):
        calls.append((model_id, device, load_after))
        return object()

    manager.schedule_convert = schedule_convert
    result = asyncio.run(
        manager.recover_model(
            MODEL_ID,
            recovery["recovery_id"],
            "restart_download",
            device="CPU",
        )
    )

    assert result["started_action"] == "restart_download"
    assert calls == [(MODEL_ID, "CPU", True)]
    assert not cache.exists()
    assert not Path(manager.catalog[MODEL_ID].model_path).exists()


def test_remove_incomplete_files_preserves_reusable_download(monkeypatch, tmp_path) -> None:
    cache = _prepare_cache(monkeypatch, tmp_path)
    manager = _manager(tmp_path)
    recovery = _prepare_interrupted_conversion(manager, tmp_path)

    result = asyncio.run(
        manager.recover_model(
            MODEL_ID,
            recovery["recovery_id"],
            "remove_incomplete_files",
        )
    )

    assert result["status"] == "cleaned"
    assert result["removed_incomplete_output"] is True
    assert cache.is_dir()
    assert result["recovery"]["downloaded_files"] == "reusable"
    assert result["recovery"]["conversion_output"] == "missing"


def test_recovery_routes_use_existing_security_and_stale_state_contract(
    monkeypatch,
    tmp_path,
) -> None:
    _prepare_cache(monkeypatch, tmp_path)
    app = create_app(_settings(tmp_path, api_key="secret-key"))
    with TestClient(app) as client:
        manager = client.app.state.manager
        recovery = _prepare_interrupted_conversion(manager, tmp_path)

        missing_key = client.get(f"/v1/models/recovery/{MODEL_ID}")
        details = client.get(
            f"/v1/models/recovery/{MODEL_ID}",
            headers={"Authorization": "Bearer secret-key"},
        )
        stale = client.post(
            "/v1/models/recovery/action",
            headers={"Authorization": "Bearer secret-key"},
            json={
                "model": MODEL_ID,
                "recovery_id": "recovery-stale",
                "action": "resume",
            },
        )
        cross_site = client.post(
            "/v1/models/recovery/action",
            headers={
                "Authorization": "Bearer secret-key",
                "Origin": "https://evil.example",
                "Sec-Fetch-Site": "cross-site",
            },
            json={
                "model": MODEL_ID,
                "recovery_id": recovery["recovery_id"],
                "action": "remove_incomplete_files",
            },
        )

    assert missing_key.status_code == 401
    assert details.status_code == 200
    assert details.json()["failure_details"]["message"] == "Conversion failed"
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "stale_recovery"
    assert cross_site.status_code == 403
