"""A conversion error must survive the automatic load preparation path."""

import asyncio

from app.config import BASE_DIR, Settings
from app.model_manager_core import ModelManager
from runtime import device_check

MODEL_ID = "tinyllama-1.1b-chat-fp16"


def test_auto_convert_error_is_not_overwritten_by_generic_load_failure(
    monkeypatch, tmp_path
) -> None:
    settings = Settings(
        device="CPU",
        models_file=BASE_DIR / "models.json",
        models_dir=tmp_path / "models",
        cache_dir=tmp_path / "cache",
        force_mock=True,
        auto_convert=True,
        default_model=None,
    )
    manager = ModelManager(settings)
    manager.force_mock = False
    monkeypatch.setattr(device_check, "available_devices", lambda: ["CPU"])
    monkeypatch.setattr(
        "app.model_manager_core.registry.is_downloaded",
        lambda _cfg, _root: False,
    )

    async def fail_conversion(model_id, _device, *, load_after):
        assert load_after is False
        manager._set_status(model_id, "error", error="Conversion failed: invalid exporter")
        manager._set_progress(
            model_id, "error", "Conversion failed: invalid exporter", append_log="invalid exporter"
        )

    def unexpected_engine(*_args, **_kwargs):
        raise AssertionError("An incomplete conversion must not be loaded")

    monkeypatch.setattr(manager, "_convert_task", fail_conversion)
    monkeypatch.setattr(manager, "_build_engine", unexpected_engine)

    asyncio.run(manager._load_task(MODEL_ID, "CPU"))

    assert manager.status_overrides[MODEL_ID]["status"] == "error"
    assert manager.status_overrides[MODEL_ID]["error"] == "Conversion failed: invalid exporter"
    assert manager.progress[MODEL_ID]["message"] == "Conversion failed: invalid exporter"
    assert manager.progress[MODEL_ID]["log_tail"] == ["invalid exporter"]
    assert MODEL_ID not in manager.engines
