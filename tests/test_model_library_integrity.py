import asyncio
from pathlib import Path

import pytest

from app import model_library as library
from app.config import Settings
from app.model_library import (
    ModelDefinitionImportRequest,
    ModelLibraryService,
)
from app.model_manager import ModelManager


def _settings(tmp_path):
    models_file = tmp_path / "config" / "models.json"
    models_file.parent.mkdir(parents=True)
    models_file.write_text("{}\n", encoding="utf-8")
    return Settings(
        host="127.0.0.1",
        port=8000,
        device="CPU",
        models_file=models_file,
        models_dir=tmp_path / "models",
        cache_dir=tmp_path / "cache",
        benchmark_results_file=tmp_path / "benchmarks" / "benchmarks.json",
        default_model=None,
        api_key=None,
        force_mock=True,
    )


def _definition(model_id="custom-small", **changes):
    value = {
        "model_id": model_id,
        "name": "Custom Small",
        "description": "Imported definition",
        "source_model": "example/custom-small",
        "backend": "openvino-genai",
        "weight_format": "fp16",
        "recommended_device": "CPU",
        "max_context_len": 4096,
        "max_output_tokens": 512,
        "trust_remote_code": False,
    }
    value.update(changes)
    return value


def _converted_dir(path: Path) -> Path:
    path.mkdir(parents=True)
    (path / "openvino_model.xml").write_text(
        "<net name='model' version='11'></net>",
        encoding="utf-8",
    )
    (path / "openvino_model.bin").write_bytes(b"openvino")
    (path / "config.json").write_text("{}", encoding="utf-8")
    return path


def test_manifest_validation_rejects_bad_definition_and_incomplete_evidence():
    malformed_catalog = {
        "bad-definition": {
            "definition": [],
            "metadata": {},
        }
    }
    malformed = {
        "schema_version": 1,
        "catalog": malformed_catalog,
        "catalog_sha256": library.catalog_checksum(malformed_catalog),
    }
    with pytest.raises(library.ManifestValidationError, match="must be an object"):
        library.validate_manifest_document(malformed)

    catalog = {
        "safe-model": {
            "definition": _definition("safe-model", trust_remote_code=True),
            "metadata": {
                "quality_score": float("nan"),
                "speed_score": float("inf"),
                "certifications": {
                    "CPU": [
                        {
                            "status": "verified",
                            "certified_at": "2026-07-01T00:00:00Z",
                            "openvino_version": "2025.2.0",
                            "driver_version": None,
                            "requested_device": "CPU",
                            "actual_device": "CPU",
                            "max_tested_context": 2048,
                            "report_reference": "docs/certification/report.json",
                            "report_sha256": "",
                        }
                    ]
                },
            },
        }
    }
    document = {
        "schema_version": 1,
        "catalog": catalog,
        "catalog_sha256": library.catalog_checksum(catalog),
    }
    normalized = library.validate_manifest_document(document)
    entry = normalized["catalog"]["safe-model"]
    assert entry["definition"]["trust_remote_code"] is False
    assert entry["metadata"]["quality_score"] == 0
    assert entry["metadata"]["speed_score"] == 0
    assert entry["metadata"]["certifications"]["CPU"] == []


def test_cpu_certification_accepts_null_driver_with_complete_provenance():
    catalog = {
        "safe-model": {
            "definition": _definition("safe-model"),
            "metadata": {
                "max_tested_context": 2048,
                "certifications": {
                    "CPU": [
                        {
                            "status": "verified",
                            "certified_at": "2026-07-24T12:00:00Z",
                            "openvino_version": "2026.2.1",
                            "openvino_genai_version": "2026.2.1.0",
                            "driver_version": None,
                            "requested_device": "CPU",
                            "actual_device": "CPU",
                            "max_tested_context": 2048,
                            "report_reference": "docs/certification/0.6.1/report.json",
                            "report_sha256": "a" * 64,
                        }
                    ]
                },
            },
        }
    }
    document = {
        "schema_version": 1,
        "catalog": catalog,
        "catalog_sha256": library.catalog_checksum(catalog),
    }

    record = library.validate_manifest_document(document)["catalog"]["safe-model"]["metadata"][
        "certifications"
    ]["CPU"][0]
    assert record["driver_version"] is None
    assert record["report_sha256"] == "a" * 64
    assert "tokens_sec" not in record


def test_definition_import_refuses_active_model_even_with_alias_key(tmp_path):
    settings = _settings(tmp_path)
    manager = ModelManager(settings)
    service = ModelLibraryService(settings, manager)
    service.import_definitions(
        ModelDefinitionImportRequest(
            payload={"models": {"active-model": _definition("active-model")}}
        )
    )
    manager.engines["active-model"] = object()

    with pytest.raises(ValueError, match="active"):
        service.import_definitions(
            ModelDefinitionImportRequest(
                payload={
                    "models": {"misleading-key": _definition("active-model", weight_format="int4")}
                },
                overwrite=True,
            )
        )

    assert manager.catalog["active-model"].weight_format == "fp16"


def test_definition_import_rolls_back_when_user_index_write_fails(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    manager = ModelManager(settings)
    service = ModelLibraryService(settings, manager)
    service.import_definitions(
        ModelDefinitionImportRequest(payload={"models": {"existing": _definition("existing")}})
    )
    original_file = settings.models_file.read_text(encoding="utf-8")
    original_user_file = service.user_file.read_text(encoding="utf-8")
    original_writer = service._write_user_ids

    def fail_for_new(values):
        if "new-model" in values:
            raise OSError("user index write failed")
        return original_writer(values)

    monkeypatch.setattr(service, "_write_user_ids", fail_for_new)
    with pytest.raises(OSError, match="user index write failed"):
        service.import_definitions(
            ModelDefinitionImportRequest(
                payload={"models": {"new-model": _definition("new-model")}}
            )
        )

    assert settings.models_file.read_text(encoding="utf-8") == original_file
    assert service.user_file.read_text(encoding="utf-8") == original_user_file
    assert set(manager.catalog) == {"existing"}


def test_failed_import_rollback_is_a_true_no_op_even_when_clock_advances(tmp_path, monkeypatch):
    """A rolled-back import must restore the user index byte-for-byte.

    Rebuilding the file would stamp a fresh ``updated_at`` and leave the index
    changed even though nothing was imported. Advancing the clock guarantees any
    re-serialization would differ from the captured original, so this regression
    fails if the rollback ever regenerates instead of restoring the snapshot.
    """
    import app.model_library_service as service_module

    settings = _settings(tmp_path)
    manager = ModelManager(settings)
    service = ModelLibraryService(settings, manager)
    service.import_definitions(
        ModelDefinitionImportRequest(payload={"models": {"existing": _definition("existing")}})
    )
    original_user_file = service.user_file.read_text(encoding="utf-8")

    # Any regeneration after this point would use a different timestamp.
    monkeypatch.setattr(service_module, "utc_now", lambda: "2099-01-01T00:00:00Z")

    original_writer = service._write_user_ids

    def fail_for_new(values):
        if "new-model" in values:
            raise OSError("user index write failed")
        return original_writer(values)

    monkeypatch.setattr(service, "_write_user_ids", fail_for_new)
    with pytest.raises(OSError, match="user index write failed"):
        service.import_definitions(
            ModelDefinitionImportRequest(
                payload={"models": {"new-model": _definition("new-model")}}
            )
        )

    assert service.user_file.read_text(encoding="utf-8") == original_user_file
    assert set(manager.catalog) == {"existing"}


def test_successful_conversion_catalog_failure_keeps_original_precision(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    manager = ModelManager(settings)
    service = ModelLibraryService(settings, manager)
    service.import_definitions(
        ModelDefinitionImportRequest(payload={"models": {"custom-small": _definition()}})
    )
    cfg = manager.catalog["custom-small"]
    _converted_dir(Path(cfg.model_path))

    async def successful_conversion(self, model_id, *_args, **_kwargs):
        self._clear_status(model_id)

    import app.model_manager as manager_module

    monkeypatch.setattr(manager_module._CoreModelManager, "_convert_task", successful_conversion)

    def fail_save(*_args, **_kwargs):
        raise OSError("catalog write failed")

    monkeypatch.setattr(manager_module.registry, "save_catalog", fail_save)
    asyncio.run(
        manager._convert_task(
            "custom-small",
            "CPU",
            False,
            weight_format="int4",
        )
    )

    assert manager.catalog["custom-small"].weight_format == "fp16"
    assert manager.status_overrides["custom-small"]["status"] == "error"
    assert "catalog" in manager.status_overrides["custom-small"]["error"].lower()
    assert not (Path(cfg.model_path) / ".ovllm-conversion.json").exists()


def test_conversion_without_ir_is_reported_as_error(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    manager = ModelManager(settings)
    service = ModelLibraryService(settings, manager)
    service.import_definitions(
        ModelDefinitionImportRequest(payload={"models": {"custom-small": _definition()}})
    )

    async def successful_but_empty(self, model_id, *_args, **_kwargs):
        self._clear_status(model_id)

    import app.model_manager as manager_module

    monkeypatch.setattr(manager_module._CoreModelManager, "_convert_task", successful_but_empty)
    asyncio.run(manager._convert_task("custom-small", "CPU", False))

    assert manager.status_overrides["custom-small"]["status"] == "error"
    assert "required OpenVINO IR" in manager.status_overrides["custom-small"]["error"]


def test_advisor_size_failure_does_not_fail_successful_conversion(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    manager = ModelManager(settings)
    service = ModelLibraryService(settings, manager)
    service.import_definitions(
        ModelDefinitionImportRequest(payload={"models": {"custom-small": _definition()}})
    )
    cfg = manager.catalog["custom-small"]

    async def successful_conversion(self, model_id, *_args, **_kwargs):
        _converted_dir(Path(self.catalog[model_id].model_path))
        self._clear_status(model_id)

    import app.model_manager as manager_module

    monkeypatch.setattr(manager_module._CoreModelManager, "_convert_task", successful_conversion)

    def fail_measurement(*_args, **_kwargs):
        raise OSError("size failed")

    monkeypatch.setattr(manager.advisor, "measure_converted_size", fail_measurement)
    asyncio.run(manager._convert_task("custom-small", "CPU", False))

    assert manager.status_overrides.get("custom-small", {}).get("status") != "error"
    assert (Path(cfg.model_path) / ".ovllm-conversion.json").is_file()
