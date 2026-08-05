import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.model_library import (
    ManifestValidationError,
    ModelDefinitionImportRequest,
    ModelLibraryService,
    catalog_checksum,
    parse_manifest_bytes,
)
from app.model_manager import ModelManager
from app.server import _index_html, create_app

ROOT = Path(__file__).resolve().parents[1]


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


def _definition(model_id="custom-small"):
    return {
        "model_id": model_id,
        "name": "Custom Small",
        "description": "Imported definition",
        "source_model": "example/custom-small",
        "backend": "openvino-genai",
        "weight_format": "int4",
        "recommended_device": "CPU",
        "max_context_len": 2048,
        "max_output_tokens": 512,
        "trust_remote_code": False,
    }


def test_manifest_checksum_rejects_tampering():
    catalog = {
        "custom-small": {
            "definition": _definition(),
            "metadata": {
                "curated": True,
                "profiles": ["balanced"],
                "minimum_ram_gb": 4,
                "minimum_disk_gb": 2,
                "license": "MIT",
                "gated": False,
                "quality_score": 50,
                "speed_score": 50,
                "certifications": {"CPU": [], "GPU": [], "NPU": []},
            },
        }
    }
    document = {
        "schema_version": 1,
        "catalog": catalog,
        "catalog_sha256": catalog_checksum(catalog),
    }
    parsed = parse_manifest_bytes(json.dumps(document).encode())
    assert parsed["catalog"]["custom-small"]["definition"]["source_model"] == "example/custom-small"

    document["catalog"]["custom-small"]["metadata"]["license"] = "Changed"
    with pytest.raises(ManifestValidationError, match="checksum"):
        parse_manifest_bytes(json.dumps(document).encode())


def test_definition_export_import_and_curated_snapshot(tmp_path):
    settings = _settings(tmp_path)
    manager = ModelManager(settings)
    service = ModelLibraryService(settings, manager)

    result = service.import_definitions(
        ModelDefinitionImportRequest(payload={"models": {"custom-small": _definition()}})
    )
    assert result["added"] == ["custom-small"]
    assert "custom-small" in manager.catalog

    exported = service.export_definitions()
    assert exported["models"]["custom-small"]["weight_format"] == "int4"

    snapshot = service.snapshot(profile="balanced")
    item = next(value for value in snapshot["items"] if value["id"] == "custom-small")
    assert item["verification"]["CPU"]["status"] == "expected_unverified"
    assert item["recommended_quantization"]["format"] in {"int4", "int8", "fp16"}


def test_model_library_routes_and_converted_import(tmp_path):
    settings = _settings(tmp_path)
    source = tmp_path / "external-model"
    source.mkdir()
    (source / "openvino_model.xml").write_text(
        "<net name='model' version='11'></net>",
        encoding="utf-8",
    )
    (source / "openvino_model.bin").write_bytes(b"openvino")
    (source / "config.json").write_text("{}", encoding="utf-8")

    app = create_app(settings)
    with TestClient(app) as client:
        library = client.get("/v1/model-library")
        assert library.status_code == 200, library.text

        imported = client.post(
            "/v1/model-library/import-converted",
            json={
                "model_id": "imported-openvino",
                "name": "Imported OpenVINO",
                "source_path": str(source.resolve()),
                "backend": "openvino-genai",
                "weight_format": "fp16",
                "recommended_device": "CPU",
                "max_context_len": 2048,
                "max_output_tokens": 512,
            },
        )
        assert imported.status_code == 200, imported.text
        assert imported.json()["conversion_health"]["status"] == "compatible"
        target = settings.models_dir / "imported-openvino"
        assert (target / "openvino_model.xml").is_file()
        assert (target / "openvino_model.bin").is_file()
        assert (target / "config.json").is_file()

        refreshed = client.get(
            "/v1/model-library",
            params={"profile": "balanced", "include_all": "true"},
        )
        assert refreshed.status_code == 200
        assert any(item["id"] == "imported-openvino" for item in refreshed.json()["items"])


def test_model_library_ui_is_composed_once():
    _index_html.cache_clear()
    html = _index_html()
    assert html.count('id="ovllm-model-library-extension"') == 1
    assert "Verified Model Library" in html
    assert "/v1/model-library/import-converted" in html


def test_bundled_manifest_and_release_wiring():
    manifest = parse_manifest_bytes((ROOT / "model_library_manifest.json").read_bytes())
    assert len(manifest["catalog"]) == 10
    certified = [
        (model_id, device, record)
        for model_id, entry in manifest["catalog"].items()
        for device, records in entry["metadata"]["certifications"].items()
        for record in records
    ]
    assert len(certified) == 5
    by_combination = {(model_id, device): record for model_id, device, record in certified}
    assert set(by_combination) == {
        ("tinyllama-1.1b-chat-fp16", "CPU"),
        ("tinyllama-1.1b-chat-int4", "CPU"),
        ("tinyllama-1.1b-chat-int4", "GPU"),
        ("qwen2.5-3b-fp16", "CPU"),
        ("qwen2.5-3b-fp16", "GPU"),
    }
    fp16_cpu = by_combination[("tinyllama-1.1b-chat-fp16", "CPU")]
    assert fp16_cpu["driver_version"] is None
    assert fp16_cpu["max_tested_context"] == 1536
    assert fp16_cpu["report_sha256"] == (
        "41590beb5ce497b37bf44189a39bc0a9fe95696c24a107b33cfeb545778f985b"
    )
    for (model_id, device), record in by_combination.items():
        assert record["report_reference"].startswith("docs/certification/")
        assert len(record["report_sha256"]) == 64
        if device == "CPU":
            assert record["driver_version"] is None
        if model_id in {"tinyllama-1.1b-chat-int4", "qwen2.5-3b-fp16"}:
            assert record["max_tested_context"] > 0

    spec = (ROOT / "packaging" / "openvino_windows_llm.spec").read_text(encoding="utf-8")
    publish = (ROOT / "scripts" / "publish_release.ps1").read_text(encoding="utf-8")
    assert 'root / "model_library_manifest.json"' in spec
    assert "validate_model_library_manifest.py" in publish
    assert "verify_release_provenance.py" in publish
    assert "release_tools.py checksums --output-dir" not in publish
    assert '"model_library_manifest.json"' in publish
