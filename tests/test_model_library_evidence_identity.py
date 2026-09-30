import json
from pathlib import Path

import pytest

from app.config import Settings
from app.model_library import ModelLibraryService
from app.model_manager import ModelManager
from app.model_registry import ModelConfig

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


def _config(model_id: str, source_model: str = "example/official") -> ModelConfig:
    return ModelConfig(
        id=model_id,
        name="Evidence Model",
        description="Evidence model",
        backend="openvino-genai",
        model_path=f"models/openvino/{model_id}",
        source_model=source_model,
        weight_format="fp16",
        recommended_device="CPU",
        max_context_len=2048,
        max_output_tokens=512,
        trust_remote_code=False,
    )


def _definition(model_id: str, source_model: str = "example/official") -> dict:
    return {
        "model_id": model_id,
        "name": "Evidence Model",
        "description": "Evidence model",
        "source_model": source_model,
        "backend": "openvino-genai",
        "weight_format": "fp16",
        "recommended_device": "CPU",
        "max_context_len": 2048,
        "max_output_tokens": 512,
        "trust_remote_code": False,
    }


def _certification(device: str, *, date: str, tokens_sec: float) -> dict:
    return {
        "status": "verified",
        "certified_at": date,
        "openvino_version": "2025.2.0",
        "openvino_genai_version": "2025.2.0",
        "driver_version": f"{device}-driver",
        "load_time_ms": 1000.0,
        "tokens_sec": tokens_sec,
        "time_to_first_token_ms": 100.0,
        "max_tested_context": 2048,
    }


def _prepare_service(tmp_path, monkeypatch, cfg: ModelConfig) -> ModelLibraryService:
    settings = _settings(tmp_path)
    manager = ModelManager(settings)
    manager.catalog[cfg.id] = cfg
    service = ModelLibraryService(settings, manager)
    monkeypatch.setattr(
        manager.advisor,
        "hardware_snapshot",
        lambda: {
            "memory": {"total_gb": 16, "available_gb": 12},
            "disk": {"free_gb": 100},
            "available_devices": ["CPU"],
            "devices": [],
        },
    )
    monkeypatch.setattr(
        manager.advisor,
        "estimate_model",
        lambda _cfg, *, device=None: {
            "parameter_count_b": 1.0,
            "precision": "fp16",
            "download_size_gb": 2.1,
            "converted_size_gb": 2.0,
            "converted_size_source": "estimated",
            "runtime_memory_gb": 3.0,
            "kv_cache_gb": 0.1,
            "first_load_seconds": 12.0,
            "first_load_source": "estimated",
            "target_device": device or "CPU",
        },
    )
    monkeypatch.setattr(service, "_local_evidence", lambda _model_id: {})
    return service


def _service_with_benchmark(tmp_path, monkeypatch, cfg, *, requested="CPU", actual="CPU", **row):
    settings = _settings(tmp_path)
    manager = ModelManager(settings)
    manager.catalog[cfg.id] = cfg
    service = ModelLibraryService(settings, manager)
    fingerprint = row.pop("hardware_fingerprint", "current-hardware")
    monkeypatch.setattr(
        manager.advisor,
        "hardware_snapshot",
        lambda: {
            "fingerprint": "current-hardware",
            "available_devices": ["CPU", "GPU", "NPU"],
            "devices": [],
            "runtime": {},
        },
    )
    result = {
        "model_id": cfg.id,
        "source_model": cfg.source_model,
        "backend": cfg.backend,
        "weight_format": cfg.weight_format,
        "requested_device": requested,
        "actual_device": actual,
        "success": True,
        **row,
    }
    settings.benchmark_results_file.parent.mkdir(parents=True)
    settings.benchmark_results_file.write_text(
        json.dumps(
            {
                "runs": [
                    {
                        "created_at": "2026-07-24T12:00:00Z",
                        "hardware_fingerprint": fingerprint,
                        "results": [result],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return service


@pytest.mark.parametrize(
    ("requested", "actual", "device", "accepted"),
    [
        ("NPU", "NPU", "NPU", True),
        ("NPU", "GPU", "NPU", False),
        ("GPU", "CPU", "GPU", False),
        ("CPU", None, "CPU", False),
        ("CPU", "unknown", "CPU", False),
        ("AUTO", "GPU", "GPU", False),
    ],
)
def test_local_evidence_requires_requested_and_actual_direct_device(
    tmp_path, monkeypatch, requested, actual, device, accepted
):
    cfg = _config("direct-device-model")
    service = _service_with_benchmark(
        tmp_path, monkeypatch, cfg, requested=requested, actual=actual
    )

    assert (device in service._local_evidence(cfg.id)) is accepted


def test_exact_local_result_outranks_bundled_evidence(tmp_path, monkeypatch):
    cfg = _config("local-npu-model")
    service = _service_with_benchmark(tmp_path, monkeypatch, cfg, requested="NPU", actual="NPU")
    local = service._local_evidence(cfg.id)

    device, reason = service._evidence_device(
        "GPU", {"CPU", "GPU", "NPU"}, local, {"GPU": {"status": "verified"}}
    )

    assert device == "NPU"
    assert "local benchmark" in reason


def test_mismatched_local_result_does_not_outrank_bundled_gpu(tmp_path, monkeypatch):
    cfg = _config("qwen2.5-3b-fp16")
    service = _service_with_benchmark(tmp_path, monkeypatch, cfg, requested="NPU", actual="GPU")
    local = service._local_evidence(cfg.id)

    device, reason = service._evidence_device(
        "GPU", {"CPU", "GPU", "NPU"}, local, {"GPU": {"status": "verified"}}
    )

    assert local == {}
    assert device == "GPU"
    assert "bundled certification" in reason


def test_local_evidence_requires_exact_weight_format(tmp_path, monkeypatch):
    cfg = _config("format-model")
    service = _service_with_benchmark(tmp_path, monkeypatch, cfg, weight_format="int4")

    assert service._local_evidence(cfg.id) == {}


def test_local_evidence_requires_current_hardware_fingerprint(tmp_path, monkeypatch):
    cfg = _config("fingerprint-model")
    service = _service_with_benchmark(
        tmp_path, monkeypatch, cfg, hardware_fingerprint="other-hardware"
    )

    assert service._local_evidence(cfg.id) == {}


def test_historical_benchmark_without_identity_remains_readable(tmp_path, monkeypatch):
    cfg = _config("historical-model")
    service = _service_with_benchmark(tmp_path, monkeypatch, cfg)
    path = service.settings.benchmark_results_file
    data = json.loads(path.read_text(encoding="utf-8"))
    for field in ("source_model", "backend", "weight_format", "actual_device"):
        data["runs"][0]["results"][0].pop(field)
    path.write_text(json.dumps(data), encoding="utf-8")

    assert len(service.manager.advisor._benchmark_rows()) == 1
    assert service._local_evidence(cfg.id) == {}


def test_official_evidence_is_not_applied_to_different_model_identity(tmp_path, monkeypatch):
    cfg = _config("identity-model", source_model="example/custom")
    service = _prepare_service(tmp_path, monkeypatch, cfg)

    entry = service._entry(
        cfg.id,
        {
            "definition": _definition(cfg.id, source_model="example/official"),
            "metadata": {
                "curated": True,
                "certifications": {
                    "CPU": [_certification("CPU", date="2026-07-01", tokens_sec=10.0)],
                    "GPU": [],
                    "NPU": [],
                },
            },
        },
    )

    assert entry is not None
    assert entry["manifest_definition_match"] is False
    assert entry["verification"]["CPU"]["status"] == "expected_unverified"
    assert entry["metrics"]["tokens_sec"] is None
    assert entry["curated"] is False


def test_metrics_use_certification_for_selected_device(tmp_path, monkeypatch):
    cfg = _config("device-model")
    service = _prepare_service(tmp_path, monkeypatch, cfg)

    entry = service._entry(
        cfg.id,
        {
            "definition": _definition(cfg.id),
            "metadata": {
                "curated": True,
                "certifications": {
                    "CPU": [_certification("CPU", date="2026-06-01", tokens_sec=8.0)],
                    "GPU": [],
                    "NPU": [_certification("NPU", date="2026-07-01", tokens_sec=20.0)],
                },
            },
        },
    )

    assert entry is not None
    assert entry["recommended_quantization"]["device"] == "CPU"
    assert entry["metrics"]["measurement_source"] == "official"
    assert entry["metrics"]["measurement_device"] == "CPU"
    assert entry["metrics"]["tokens_sec"] == 8.0
    assert entry["metrics"]["tested_driver_version"] == "CPU-driver"


def test_local_metrics_do_not_inherit_certification_from_another_device(
    tmp_path, monkeypatch
):
    cfg = _config("cross-device-evidence-model")
    service = _prepare_service(tmp_path, monkeypatch, cfg)
    monkeypatch.setattr(
        service,
        "_local_evidence",
        lambda _model_id: {
            "CPU": {
                "status": "locally_verified",
                "actual_device": "CPU",
                "openvino_version": "2026.2.0",
                "driver_version": "CPU-local-driver",
                "load_time_ms": None,
                "tokens_sec": 7.5,
                "time_to_first_token_ms": None,
                "tested_at": "2026-09-30T12:00:00Z",
            }
        },
    )

    entry = service._entry(
        cfg.id,
        {
            "definition": _definition(cfg.id),
            "metadata": {
                "curated": True,
                "max_tested_context": 2048,
                "certifications": {
                    "CPU": [],
                    "GPU": [_certification("GPU", date="2026-07-01", tokens_sec=20.0)],
                    "NPU": [],
                },
            },
        },
    )

    assert entry is not None
    assert entry["verification"]["GPU"]["status"] == "verified"
    assert entry["metrics"]["measurement_source"] == "local"
    assert entry["metrics"]["measurement_device"] == "CPU"
    assert entry["metrics"]["tokens_sec"] == 7.5
    assert entry["metrics"]["time_to_first_load_ms"] is None
    assert entry["metrics"]["time_to_first_token_ms"] is None
    assert entry["metrics"]["maximum_tested_context"] is None
    assert entry["metrics"]["last_certification_date"] is None
    assert entry["metrics"]["tested_driver_version"] == "CPU-local-driver"


def test_browser_displays_measurement_device():
    source = (ROOT / "app" / "model_library_ui.py").read_text(encoding="utf-8")

    assert "metrics.measurement_device" in source
    assert "measurementSource" in source


def test_verified_gpu_outranks_unverified_preferred_npu():
    device, reason = ModelLibraryService._evidence_device(
        "NPU",
        {"CPU", "GPU", "NPU"},
        {},
        {
            "CPU": {"status": "verified"},
            "GPU": {"status": "verified"},
            "NPU": {"status": "expected_unverified"},
        },
    )
    assert device == "GPU"
    assert "bundled certification" in reason


def test_local_npu_outranks_bundled_gpu():
    device, reason = ModelLibraryService._evidence_device(
        "GPU",
        {"CPU", "GPU", "NPU"},
        {"NPU": {"status": "locally_verified"}},
        {"GPU": {"status": "verified"}},
    )
    assert device == "NPU"
    assert "local benchmark" in reason


def test_auto_is_not_direct_device_evidence():
    device, _ = ModelLibraryService._evidence_device(
        "AUTO",
        {"CPU", "AUTO"},
        {"AUTO": {"status": "locally_verified"}},
        {},
    )
    assert device == "CPU"
