from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.hardware_advisor import (
    HardwareAdvisor,
    infer_parameter_count_b,
    normalize_profile,
    parse_auto_model,
)
from app.model_registry import ModelConfig


def make_cfg(
    model_id: str,
    *,
    name: str | None = None,
    backend: str = "openvino-genai",
    precision: str = "fp16",
    device: str = "CPU",
    context: int = 4096,
    output: int = 512,
) -> ModelConfig:
    return ModelConfig(
        id=model_id,
        name=name or model_id,
        description="test model",
        backend=backend,
        model_path=f"models/openvino/{model_id}",
        source_model=f"example/{model_id}",
        weight_format=precision,
        recommended_device=device,
        max_context_len=context,
        max_output_tokens=output if "embedding" not in backend else 0,
    )


def make_snapshot(*, ram=32.0, available=28.0, disk=100.0, devices=("CPU", "GPU", "NPU")):
    return {
        "fingerprint": "test-hardware",
        "os": {"system": "Windows", "release": "11"},
        "cpu": {"name": "Intel test CPU", "physical_cores": 8, "logical_cores": 16},
        "memory": {"total_gb": ram, "available_gb": available, "used_percent": 12.5},
        "disk": {"free_gb": disk, "total_gb": 500.0, "models_gb": 0.0},
        "gpu": {"total_gb": 16.0},
        "devices": [
            {"device": item, "base": item, "driver_version": "test-driver"} for item in devices
        ],
        "available_devices": list(devices),
        "runtime": {"openvino": "test", "openvino_genai": "test", "mock": False},
    }


def make_advisor(tmp_path, catalog):
    settings = SimpleNamespace(
        models_dir=tmp_path / "models",
        benchmark_results_file=tmp_path / "benchmarks.json",
    )
    return HardwareAdvisor(settings, catalog)


def test_parameter_count_parser_uses_explicit_model_size():
    assert infer_parameter_count_b("qwen2.5-1.5b-instruct") == pytest.approx(1.5)
    assert infer_parameter_count_b("smollm2-135m-instruct") == pytest.approx(0.135)
    assert infer_parameter_count_b("microsoft/phi-4-mini-instruct") == pytest.approx(3.8)


def test_auto_aliases_and_profile_names_are_normalized():
    assert parse_auto_model("auto") == "balanced"
    assert parse_auto_model("AUTO:best_quality") == "best-quality"
    assert normalize_profile("low power") == "lowest-power"
    assert parse_auto_model("qwen2.5-1.5b") is None
    with pytest.raises(ValueError):
        parse_auto_model("auto:unknown")


def test_preflight_blocks_model_when_ram_and_disk_are_insufficient(tmp_path, monkeypatch):
    cfg = make_cfg("qwen2.5-14b-fp16", device="CPU")
    advisor = make_advisor(tmp_path, {cfg.id: cfg})
    snapshot = make_snapshot(ram=16, available=4, disk=5, devices=("CPU",))
    monkeypatch.setattr(advisor, "hardware_snapshot", lambda refresh=False: snapshot)
    monkeypatch.setattr(advisor, "_actual_converted_size_gb", lambda _cfg: None)
    monkeypatch.setattr(advisor, "_latest_benchmark", lambda *_args, **_kwargs: None)

    evaluation = advisor.evaluate_model(cfg, snapshot=snapshot)

    assert evaluation["compatibility"] == "blocked"
    assert evaluation["requires_confirmation"] is True
    codes = {warning["code"] for warning in evaluation["warnings"]}
    assert "disk-insufficient" in codes
    assert "ram-insufficient" in codes


def test_profiles_produce_distinct_model_recommendations(tmp_path, monkeypatch):
    small = make_cfg("qwen2.5-0.5b-fp16", device="NPU")
    large = make_cfg("qwen2.5-14b-fp16", device="CPU")
    advisor = make_advisor(tmp_path, {small.id: small, large.id: large})
    snapshot = make_snapshot(ram=96, available=82, disk=300)
    monkeypatch.setattr(advisor, "hardware_snapshot", lambda refresh=False: snapshot)
    monkeypatch.setattr(advisor, "_actual_converted_size_gb", lambda _cfg: None)
    monkeypatch.setattr(advisor, "_latest_benchmark", lambda *_args, **_kwargs: None)

    fastest = advisor.recommend_profile("fastest", snapshot=snapshot)
    quality = advisor.recommend_profile("best-quality", snapshot=snapshot)
    memory = advisor.recommend_profile("lowest-memory", snapshot=snapshot)

    assert fastest["model_id"] == small.id
    assert memory["model_id"] == small.id
    assert quality["model_id"] == large.id
    assert quality["context_length"] <= large.max_context_len
    assert quality["output_tokens"] <= large.max_output_tokens


def test_auto_selection_uses_only_loaded_generation_models(tmp_path, monkeypatch):
    text = make_cfg("qwen2.5-1.5b-fp16", device="NPU")
    embedding = make_cfg(
        "bge-small-en-v1.5",
        backend="openvino-embeddings",
        device="CPU",
        context=512,
        output=0,
    )
    advisor = make_advisor(tmp_path, {text.id: text, embedding.id: embedding})
    snapshot = make_snapshot()
    monkeypatch.setattr(advisor, "hardware_snapshot", lambda refresh=False: snapshot)
    monkeypatch.setattr(advisor, "_actual_converted_size_gb", lambda _cfg: None)
    monkeypatch.setattr(advisor, "_latest_benchmark", lambda *_args, **_kwargs: None)

    engines = {text.id: object(), embedding.id: object()}
    selected = advisor.select_loaded_model(
        "balanced",
        engines,
        {text.id: "NPU", embedding.id: "CPU"},
    )

    assert selected == text.id


def test_benchmark_store_rows_keep_hardware_evidence(tmp_path):
    cfg = make_cfg("qwen2.5-1.5b-fp16")
    advisor = make_advisor(tmp_path, {cfg.id: cfg})
    run = {
        "run_id": "auto-test",
        "created_at": "2026-07-21T12:00:00Z",
        "automatic": True,
        "hardware_fingerprint": "test-hardware",
        "results": [
            {
                "model_id": cfg.id,
                "requested_device": "NPU",
                "success": True,
                "tokens_sec": 18.5,
            }
        ],
    }

    advisor._append_run(run)
    rows = advisor._benchmark_rows()

    assert rows[0]["automatic"] is True
    assert rows[0]["hardware_fingerprint"] == "test-hardware"
    assert rows[0]["tokens_sec"] == 18.5


def test_loaded_model_is_not_rejected_by_post_load_available_ram(tmp_path, monkeypatch):
    cfg = make_cfg("qwen2.5-3b-fp16", device="NPU")
    advisor = make_advisor(tmp_path, {cfg.id: cfg})
    snapshot = make_snapshot(ram=16, available=1, disk=100)
    monkeypatch.setattr(advisor, "hardware_snapshot", lambda refresh=False: snapshot)
    monkeypatch.setattr(advisor, "_actual_converted_size_gb", lambda _cfg: 6.0)
    monkeypatch.setattr(advisor, "_latest_benchmark", lambda *_args, **_kwargs: None)

    evaluation = advisor.evaluate_model(
        cfg, downloaded=True, loaded=True, loaded_device="NPU", snapshot=snapshot
    )

    assert evaluation["compatibility"] != "blocked"


def test_embedding_advisor_output_budget_is_zero(tmp_path, monkeypatch):
    cfg = make_cfg(
        "bge-small-en-v1.5", backend="openvino-embeddings", device="CPU", context=512, output=0
    )
    advisor = make_advisor(tmp_path, {cfg.id: cfg})
    snapshot = make_snapshot(devices=("CPU",))
    monkeypatch.setattr(advisor, "hardware_snapshot", lambda refresh=False: snapshot)
    monkeypatch.setattr(advisor, "_actual_converted_size_gb", lambda _cfg: None)
    monkeypatch.setattr(advisor, "_latest_benchmark", lambda *_args, **_kwargs: None)

    evaluation = advisor.evaluate_model(cfg, snapshot=snapshot)

    assert evaluation["recommended_output_tokens"] == 0


def test_expanded_openvino_catalog_has_distinct_safe_model_variants():
    from app.config import BASE_DIR
    from app.model_registry import load_catalog

    catalog = load_catalog(BASE_DIR / "models.json")
    expected = {
        "qwen2.5-coder-1.5b-int4": ("Qwen/Qwen2.5-Coder-1.5B-Instruct", "int4"),
        "qwen2.5-coder-7b-int4": ("Qwen/Qwen2.5-Coder-7B-Instruct", "int4"),
        "qwen3-4b-int4": ("Qwen/Qwen3-4B", "int4"),
        "qwen3-8b-int4": ("Qwen/Qwen3-8B", "int4"),
        "qwen3-30b-a3b-int4": ("Qwen/Qwen3-30B-A3B", "int4"),
        "qwen3-30b-a3b-int8": ("Qwen/Qwen3-30B-A3B", "int8"),
        "qwen2.5-coder-32b-int8": ("Qwen/Qwen2.5-Coder-32B-Instruct", "int8"),
    }
    assert len(catalog) >= 50
    for model_id, (source, precision) in expected.items():
        cfg = catalog[model_id]
        assert cfg.source_model == source
        assert cfg.weight_format == precision
        assert cfg.backend == "openvino-genai"
        assert cfg.trust_remote_code is False
        assert cfg.model_path == f"models/openvino/{model_id}"
        assert cfg.max_output_tokens < cfg.max_context_len
    assert catalog["qwen3-30b-a3b-int8"].recommended_device == "CPU"
    assert catalog["qwen2.5-coder-32b-int8"].recommended_device == "CPU"


@pytest.mark.parametrize(
    "model_id",
    ["qwen3-30b-a3b-int8", "qwen2.5-coder-32b-int8"],
)
def test_new_high_memory_models_block_on_32gb_host(tmp_path, monkeypatch, model_id):
    from app.config import BASE_DIR
    from app.model_registry import load_catalog

    catalog = load_catalog(BASE_DIR / "models.json")
    advisor = make_advisor(tmp_path, catalog)
    monkeypatch.setattr(advisor, "_actual_converted_size_gb", lambda _cfg: None)
    monkeypatch.setattr(advisor, "_latest_benchmark", lambda *_a, **_kw: None)
    cfg = catalog[model_id]

    estimates = advisor.estimate_model(cfg, device="CPU")
    assert estimates["parameter_count_b"] >= 30  # MoE uses total weights, not active experts
    assert estimates["runtime_memory_gb"] > 32
    insufficient = make_snapshot(ram=32, available=26, disk=700, devices=("CPU",))
    evaluation = advisor.evaluate_model(cfg, snapshot=insufficient)
    assert evaluation["compatibility"] == "blocked"
    assert "ram-insufficient" in {warning["code"] for warning in evaluation["warnings"]}

    roomy = make_snapshot(ram=128, available=110, disk=700, devices=("CPU",))
    capable = advisor.evaluate_model(cfg, snapshot=roomy)
    assert "ram-insufficient" not in {warning["code"] for warning in capable["warnings"]}
