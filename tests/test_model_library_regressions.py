import asyncio
import json
import os
import subprocess
from pathlib import Path

import pytest

from app import model_library as library
from app.config import Settings
from app.model_library import (
    ConvertedModelImportRequest,
    ManifestValidationError,
    ModelDefinitionImportRequest,
    ModelLibraryService,
    conversion_health,
)
from app.model_library_conversion import is_reparse_point
from app.model_manager import ModelManager
from app.model_registry import ModelConfig, load_catalog


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


def _config(model_id: str, model_dir: Path) -> ModelConfig:
    return ModelConfig(
        id=model_id,
        name=model_id,
        description="test",
        backend="openvino-genai",
        model_path=str(model_dir.resolve()),
        source_model="example/source",
        weight_format="fp16",
        recommended_device="CPU",
        max_context_len=2048,
        max_output_tokens=512,
    )


def test_conversion_health_handles_non_object_and_incomplete_metadata(tmp_path):
    model_dir = _converted_dir(tmp_path / "converted")
    cfg = _config("broken-marker", model_dir)
    marker = model_dir / ".ovllm-conversion.json"

    marker.write_text("[]", encoding="utf-8")
    assert conversion_health(cfg)["status"] == "invalid_metadata"

    marker.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "model_id": cfg.id,
                "source_model": cfg.source_model,
            }
        ),
        encoding="utf-8",
    )
    result = conversion_health(cfg)
    assert result["status"] == "invalid_metadata"
    assert "missing" in result["details"].lower()


def test_conversion_health_detects_openvino_genai_runtime_change(tmp_path, monkeypatch):
    model_dir = _converted_dir(tmp_path / "converted")
    cfg = _config("stale-genai", model_dir)
    (model_dir / ".ovllm-conversion.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "model_id": cfg.id,
                "source_model": cfg.source_model,
                "backend": cfg.backend,
                "weight_format": cfg.weight_format,
                "application_version": "1.0.0",
                "openvino_version": "2025.1.0",
                "openvino_genai_version": "2025.1.0",
                "recorded_at": "2026-07-23T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    versions = {"openvino": "2025.1.9", "openvino-genai": "2025.2.0"}
    monkeypatch.setattr(library, "_package_version", lambda name: versions.get(name))

    result = conversion_health(cfg)
    assert result["status"] == "stale_runtime"
    assert "OpenVINO GenAI" in result["details"]


def test_definition_import_is_atomic_when_later_entry_is_invalid(tmp_path):
    settings = _settings(tmp_path)
    manager = ModelManager(settings)
    service = ModelLibraryService(settings, manager)

    with pytest.raises(ValueError):
        service.import_definitions(
            ModelDefinitionImportRequest(
                payload={
                    "models": {
                        "valid-first": _definition("valid-first"),
                        "invalid-second": _definition(
                            "invalid-second", recommended_device="BANANA"
                        ),
                    }
                }
            )
        )

    assert manager.catalog == {}
    assert load_catalog(settings.models_file) == {}
    assert not service.user_file.exists()


def test_official_catalog_does_not_replace_user_owned_definition(tmp_path):
    settings = _settings(tmp_path)
    manager = ModelManager(settings)
    service = ModelLibraryService(settings, manager)
    service.import_definitions(
        ModelDefinitionImportRequest(payload={"models": {"custom-small": _definition()}})
    )

    result = service.apply_official_definitions(
        {
            "catalog": {
                "custom-small": {
                    "definition": _definition("custom-small", weight_format="int4"),
                    "metadata": {},
                }
            }
        }
    )

    assert result["conflicts"] == ["custom-small"]
    assert manager.catalog["custom-small"].weight_format == "fp16"


def test_official_certification_metrics_are_used_without_local_benchmark(tmp_path):
    settings = _settings(tmp_path)
    manager = ModelManager(settings)
    service = ModelLibraryService(settings, manager)
    service.import_definitions(
        ModelDefinitionImportRequest(payload={"models": {"custom-small": _definition()}})
    )
    entry = service._entry(
        "custom-small",
        {
            "metadata": {
                "curated": True,
                "profiles": ["fastest"],
                "minimum_ram_gb": 4,
                "minimum_disk_gb": 2,
                "license": "Apache-2.0",
                "gated": False,
                "quality_score": 60,
                "speed_score": 70,
                "max_tested_context": 0,
                "certifications": {
                    "CPU": [
                        {
                            "status": "verified",
                            "certified_at": "2026-06-01T00:00:00Z",
                            "openvino_version": "2025.1.0",
                            "driver_version": "old",
                            "load_time_ms": 9000.0,
                            "tokens_sec": 5.0,
                            "time_to_first_token_ms": 800.0,
                            "max_tested_context": 2048,
                        },
                        {
                            "status": "verified",
                            "certified_at": "2026-07-01T00:00:00Z",
                            "openvino_version": "2025.2.0",
                            "driver_version": "new",
                            "load_time_ms": 4000.0,
                            "tokens_sec": 12.5,
                            "time_to_first_token_ms": 250.0,
                            "max_tested_context": 4096,
                        },
                    ],
                    "GPU": [],
                    "NPU": [],
                },
            }
        },
    )

    assert entry is not None
    assert entry["verification"]["CPU"]["certified_at"] == "2026-07-01T00:00:00Z"
    assert entry["metrics"]["tokens_sec"] == 12.5
    assert entry["metrics"]["time_to_first_load_ms"] == 4000.0
    assert entry["metrics"]["time_to_first_token_ms"] == 250.0
    assert entry["metrics"]["maximum_tested_context"] == 4096
    assert entry["metrics"]["measurement_source"] == "official"


def test_converted_import_rejects_root_symlink_and_existing_id(tmp_path):
    settings = _settings(tmp_path)
    manager = ModelManager(settings)
    service = ModelLibraryService(settings, manager)
    real_source = _converted_dir(tmp_path / "real-source")
    linked_source = tmp_path / "linked-source"
    try:
        linked_source.symlink_to(real_source, target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlinks are unavailable in this test environment")

    with pytest.raises(ValueError, match="symbolic links"):
        service.import_converted(
            ConvertedModelImportRequest(
                model_id="symlinked-model",
                name="Symlinked Model",
                source_path=str(linked_source.absolute()),
            )
        )

    service.import_definitions(
        ModelDefinitionImportRequest(payload={"models": {"custom-small": _definition()}})
    )
    with pytest.raises(ValueError, match="already registered"):
        service.import_converted(
            ConvertedModelImportRequest(
                model_id="custom-small",
                name="Existing Model",
                source_path=str(real_source.resolve()),
            )
        )


def test_converted_import_rejects_junction_source(tmp_path):
    """Windows junctions are reparse points and must be rejected on import.

    The directory-symlink test above skips wherever creating a symlink needs
    elevation (the default on stock Windows). Junctions are also reparse points
    but can be created without elevation via ``mklink /J``, so this exercises
    the reparse-point guard on a normal Windows install instead of skipping.
    """
    if os.name != "nt":
        pytest.skip("Junctions are a Windows-only reparse point.")
    settings = _settings(tmp_path)
    manager = ModelManager(settings)
    service = ModelLibraryService(settings, manager)
    real_source = _converted_dir(tmp_path / "real-source")
    junction = tmp_path / "junction-source"
    created = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(real_source)],
        capture_output=True,
        text=True,
    )
    if created.returncode != 0 or not is_reparse_point(junction):
        pytest.skip("A directory junction could not be created in this environment.")

    with pytest.raises(ValueError, match="symbolic links"):
        service.import_converted(
            ConvertedModelImportRequest(
                model_id="junction-model",
                name="Junction Model",
                source_path=str(junction.absolute()),
            )
        )
    # The rejected import must not register a catalog entry or copy any files.
    assert "junction-model" not in manager.catalog
    assert not (settings.models_dir / "junction-model").exists()


def test_converted_import_rolls_back_files_when_catalog_save_fails(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    manager = ModelManager(settings)
    service = ModelLibraryService(settings, manager)
    source = _converted_dir(tmp_path / "source")

    def fail_save(*_args, **_kwargs):
        raise OSError("catalog write failed")

    monkeypatch.setattr(library.registry, "save_catalog", fail_save)
    with pytest.raises(OSError, match="catalog write failed"):
        service.import_converted(
            ConvertedModelImportRequest(
                model_id="rollback-model",
                name="Rollback Model",
                source_path=str(source.resolve()),
            )
        )

    assert not (settings.models_dir / "rollback-model").exists()
    assert "rollback-model" not in manager.catalog


def test_failed_requantization_does_not_relabel_existing_conversion(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    manager = ModelManager(settings)
    service = ModelLibraryService(settings, manager)
    service.import_definitions(
        ModelDefinitionImportRequest(payload={"models": {"custom-small": _definition()}})
    )
    cfg = manager.catalog["custom-small"]
    _converted_dir(Path(cfg.model_path))

    async def failed_conversion(self, model_id, *_args, **_kwargs):
        self._set_status(model_id, "error", error="conversion failed")

    import app.model_manager as manager_module

    monkeypatch.setattr(manager_module._CoreModelManager, "_convert_task", failed_conversion)
    asyncio.run(
        manager._convert_task(
            "custom-small",
            "CPU",
            False,
            weight_format="int4",
        )
    )

    assert manager.catalog["custom-small"].weight_format == "fp16"
    assert not (Path(cfg.model_path) / ".ovllm-conversion.json").exists()


def test_official_refresh_stops_reading_oversized_manifest(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    manager = ModelManager(settings)
    service = ModelLibraryService(settings, manager)

    class FakeResponse:
        url = library.OFFICIAL_MANIFEST_URL
        headers = {}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def raise_for_status(self):
            return None

        async def aiter_bytes(self):
            yield b"x" * (library.MAX_MANIFEST_BYTES + 1)

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def stream(self, *_args, **_kwargs):
            return FakeResponse()

    monkeypatch.setattr(library.httpx, "AsyncClient", lambda **_kwargs: FakeClient())

    with pytest.raises(ManifestValidationError, match="1 MB"):
        asyncio.run(service.refresh_official())
    assert not service.cache_file.exists()
