"""Per-model KV-cache precision: validation, persistence, plugin config, estimates, API.

KV_CACHE_PRECISION is an OpenVINO CPU/GPU plugin property (u8, u4, f16; Auto
leaves OpenVINO's own choice). The NPU compiles its own static KV layout, so the
property must never reach an NPU, AUTO or composite device configuration.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import context_configuration, errors
from app.config import Settings
from app.model_registry import load_catalog, save_catalog
from app.server import create_app
from runtime import npu_compat
from runtime.openvino_engine import MockEngine, kv_cache_precision_property

MODEL = "kv-precision-model"
ARCH = {
    "max_position_embeddings": 32768,
    "num_hidden_layers": 4,
    "num_key_value_heads": 2,
    "num_attention_heads": 8,
    "hidden_size": 512,
}


def _settings(tmp_path: Path, *, metadata: dict | None = ARCH, extra: dict | None = None):
    model_dir = tmp_path / "models" / MODEL
    model_dir.mkdir(parents=True)
    if metadata is not None:
        (model_dir / "config.json").write_text(json.dumps(metadata), encoding="utf-8")
    entry = {
        "name": "KV precision model",
        "model_path": str(model_dir),
        "source_model": "org/kv-model",
        "backend": "openvino-genai",
        "weight_format": "int4",
        "recommended_device": "NPU",
        "max_context_len": 4096,
        "max_output_tokens": 1024,
        **(extra or {}),
    }
    catalog_file = tmp_path / "models.json"
    catalog_file.write_text(json.dumps({MODEL: entry}), encoding="utf-8")
    return Settings(
        models_file=catalog_file,
        models_dir=tmp_path / "models",
        cache_dir=tmp_path / "cache",
        benchmark_results_file=tmp_path / "benchmarks.json",
        default_model=None,
        device="CPU",
        force_mock=True,
    )


def _post(client, *, tokens=4096, expected=4096, precision=None):
    body = {"model": MODEL, "context_tokens": tokens, "expected_context_tokens": expected}
    if precision is not None:
        body["kv_cache_precision"] = precision
    return client.post("/v1/models/context", json=body)


# --- validation and estimates ------------------------------------------------


def test_normalize_accepts_only_documented_values() -> None:
    normalize = context_configuration.normalize_kv_cache_precision
    assert normalize(None) is None
    assert normalize("auto") is None
    assert normalize(" U8 ") == "u8"
    assert normalize("u4") == "u4"
    assert normalize("f16") == "f16"
    for bad in ("int4", "bf16", "i8", "fp8"):
        with pytest.raises(context_configuration.ContextValidationError, match="Auto, f16"):
            normalize(bad)


def test_kv_estimate_scales_with_precision() -> None:
    fp16 = context_configuration.kv_cache_gib(ARCH, 32768)
    assert fp16 == pytest.approx(0.0625, abs=0.001)
    assert context_configuration.kv_cache_gib(ARCH, 32768, "f16") == fp16
    # Auto/None is shown at the FP16 upper bound.
    assert context_configuration.kv_cache_gib(ARCH, 32768, None) == fp16
    assert context_configuration.kv_cache_gib(ARCH, 32768, "u8") == pytest.approx(
        fp16 * 1.125 / 2, abs=0.001
    )
    assert context_configuration.kv_cache_gib(ARCH, 32768, "u4") == pytest.approx(
        fp16 * 0.625 / 2, abs=0.001
    )
    # NPU never takes the property, so the effective estimate stays FP16.
    assert context_configuration.effective_kv_bytes("u4", "NPU") == 2.0
    assert context_configuration.effective_kv_bytes("u4", "GPU.1") == 0.625


def test_device_support_is_cpu_or_gpu_only() -> None:
    supported = context_configuration.kv_precision_device_supported
    assert supported("CPU") and supported("GPU") and supported("GPU.1")
    for device in ("NPU", "AUTO", "HETERO:GPU,CPU", "AUTO:GPU,CPU", "", None):
        assert not supported(device), device


# --- plugin config -------------------------------------------------------------


@pytest.mark.parametrize("device", ["CPU", "GPU", "GPU.1"])
def test_plugin_config_sends_kv_precision_to_cpu_and_gpu(tmp_path, device) -> None:
    config = npu_compat.build_plugin_config(device, 3072, tmp_path, kv_cache_precision="u4")
    assert config["KV_CACHE_PRECISION"] == "u4"
    assert "MAX_PROMPT_LEN" not in config


@pytest.mark.parametrize("device", ["NPU", "AUTO", "HETERO:GPU,CPU"])
def test_plugin_config_never_sends_kv_precision_to_npu_or_auto(tmp_path, device) -> None:
    config = npu_compat.build_plugin_config(
        device, 3072, tmp_path, max_response_len=1024, kv_cache_precision="u8"
    )
    assert "KV_CACHE_PRECISION" not in config


def test_npu_context_recompiles_with_prompt_and_response_halves(tmp_path) -> None:
    config = npu_compat.build_plugin_config(
        "NPU", 8192 - 1024, tmp_path, max_response_len=1024, kv_cache_precision="u4"
    )
    assert config["MAX_PROMPT_LEN"] == 7168
    assert config["MIN_RESPONSE_LEN"] == 1024
    assert "KV_CACHE_PRECISION" not in config


def test_plugin_config_auto_embeddings_and_bad_values(tmp_path) -> None:
    assert "KV_CACHE_PRECISION" not in npu_compat.build_plugin_config(
        "GPU", None, tmp_path, kv_cache_precision=None
    )
    assert "KV_CACHE_PRECISION" not in npu_compat.build_plugin_config(
        "GPU", None, tmp_path, kv_cache_precision="auto"
    )
    assert "KV_CACHE_PRECISION" not in npu_compat.build_plugin_config(
        "GPU", None, tmp_path, backend="openvino-embeddings", kv_cache_precision="u8"
    )
    with pytest.raises(ValueError, match="Unsupported KV cache precision"):
        kv_cache_precision_property("GPU", "int4")


# --- persistence ---------------------------------------------------------------


def test_precision_round_trips_and_corrupt_values_never_drop_the_model(tmp_path) -> None:
    settings = _settings(tmp_path, extra={"kv_cache_precision": "u4"})
    catalog = load_catalog(settings.models_file)
    assert catalog[MODEL].kv_cache_precision == "u4"
    save_catalog(settings.models_file, catalog)
    assert json.loads(settings.models_file.read_text())[MODEL]["kv_cache_precision"] == "u4"

    raw = json.loads(settings.models_file.read_text())
    raw[MODEL]["kv_cache_precision"] = "bogus"
    settings.models_file.write_text(json.dumps(raw), encoding="utf-8")
    catalog = load_catalog(settings.models_file)
    assert MODEL in catalog and catalog[MODEL].kv_cache_precision is None
    save_catalog(settings.models_file, catalog)
    assert "kv_cache_precision" not in json.loads(settings.models_file.read_text())[MODEL]


# --- API -------------------------------------------------------------------------


def test_api_sets_and_clears_precision_for_unloaded_model(tmp_path) -> None:
    settings = _settings(tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        details = client.get("/v1/models/context", params={"model": MODEL}).json()
        assert details["kv_cache_precision"] == "auto"
        assert [row["value"] for row in details["kv_cache_precision_options"]] == [
            "auto",
            "f16",
            "u8",
            "u4",
        ]
        assert all(row["available"] for row in details["kv_cache_precision_options"])
        assert "NPU-safe default" in details["context_guidance"]
        assert "32,768" in details["context_guidance"]

        applied = _post(client, precision="u8")
        assert applied.status_code == 200, applied.text
        context = applied.json()["context"]
        assert context["kv_cache_precision"] == "u8"
        option = next(row for row in context["options"] if row["tokens"] == 4096)
        assert option["kv_cache_gib_estimate"] < option["fp16_kv_cache_gib_estimate"]
        summary = next(
            row
            for row in client.get("/v1/models/contexts").json()["data"]
            if row["model_id"] == MODEL
        )
        assert summary["kv_cache_precision"] == "u8"
        assert load_catalog(settings.models_file)[MODEL].kv_cache_precision == "u8"

        refused = _post(client, precision="int4")
        assert refused.status_code == 422
        assert load_catalog(settings.models_file)[MODEL].kv_cache_precision == "u8"

        cleared = _post(client, precision="auto")
        assert cleared.status_code == 200
        assert cleared.json()["context"]["kv_cache_precision"] == "auto"
    assert load_catalog(settings.models_file)[MODEL].kv_cache_precision is None


def test_load_with_precision_persists_before_loading(tmp_path) -> None:
    settings = _settings(tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.post(
            "/v1/models/load",
            json={
                "model": MODEL,
                "device": "GPU",
                "context_tokens": 16384,
                "kv_cache_precision": "u4",
            },
        )
        assert response.status_code == 200, response.text
        cfg = app.state.manager.catalog[MODEL]
        assert (cfg.max_context_len, cfg.kv_cache_precision) == (16384, "u4")
    assert load_catalog(settings.models_file)[MODEL].kv_cache_precision == "u4"


def test_loaded_gpu_engine_rebuilds_with_precision(tmp_path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    app = create_app(settings)
    built: list[dict] = []

    def build(**kwargs):
        built.append(kwargs)
        return MockEngine(MODEL, device=kwargs["device"])

    monkeypatch.setattr("app.model_manager.create_engine", build)
    with TestClient(app) as client:
        manager = app.state.manager
        old = MockEngine(MODEL, device="GPU")
        manager.engines[MODEL] = old
        manager.devices[MODEL] = "GPU"
        applied = _post(client, precision="u4")
        assert applied.status_code == 200, applied.text
        assert built and built[0]["kv_cache_precision"] == "u4"
        assert built[0]["device"] == "GPU"
        assert manager.engines[MODEL] is not old
        loaded = client.post("/v1/models/load", json={"model": MODEL, "kv_cache_precision": "u8"})
        assert loaded.status_code == 409
        assert "Apply now" in loaded.json()["detail"]


def test_loaded_npu_engine_is_not_rebuilt_for_precision_only(tmp_path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    app = create_app(settings)
    built: list[dict] = []

    def build(**kwargs):
        built.append(kwargs)
        return MockEngine(MODEL, device=kwargs["device"])

    monkeypatch.setattr("app.model_manager.create_engine", build)
    with TestClient(app) as client:
        manager = app.state.manager
        old = MockEngine(MODEL, device="NPU")
        manager.engines[MODEL] = old
        manager.devices[MODEL] = "NPU"
        details = client.get("/v1/models/context", params={"model": MODEL}).json()
        assert details["kv_cache_precision_applies"] is False
        assert [row["available"] for row in details["kv_cache_precision_options"]] == [
            True,
            False,
            False,
            False,
        ]
        assert "unavailable" in details["kv_cache_precision_note"]

        saved = _post(client, precision="u8")
        assert saved.status_code == 200, saved.text
        assert built == [] and manager.engines[MODEL] is old
        assert manager.catalog[MODEL].kv_cache_precision == "u8"

        resized = _post(client, tokens=8192, expected=4096)
        assert resized.status_code == 200, resized.text
        assert built[-1]["max_prompt_len"] == 8192 - 1024
        assert built[-1]["max_response_len"] == 1024
        assert built[-1]["device"] == "NPU"


def test_runtime_rejection_is_friendly_and_keeps_engine(tmp_path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    app = create_app(settings)

    def reject(**kwargs):
        raise RuntimeError(
            "Exception from src\\inference\\src\\cpp\\core.cpp:112: "
            "Unsupported property KV_CACHE_PRECISION by CPU plugin"
        )

    monkeypatch.setattr("app.model_manager.create_engine", reject)
    with TestClient(app) as client:
        manager = app.state.manager
        old = MockEngine(MODEL, device="CPU")
        manager.engines[MODEL] = old
        manager.devices[MODEL] = "CPU"
        refused = _post(client, precision="u4")
        assert refused.status_code == 409
        detail = refused.json()["detail"]
        assert "KV cache precision" in detail and "Auto" in detail
        assert "core.cpp" not in detail
        assert manager.engines[MODEL] is old
        assert manager.catalog[MODEL].kv_cache_precision is None
    assert load_catalog(settings.models_file)[MODEL].kv_cache_precision is None


def test_load_error_classifies_kv_precision_rejection() -> None:
    message = errors.format_model_load_error(
        RuntimeError("Unsupported property KV_CACHE_PRECISION"), device="GPU"
    )
    assert "KV cache precision to Auto" in message and "load again" in message


def test_presets_offer_max_for_this_model(tmp_path) -> None:
    settings = _settings(tmp_path, metadata={**ARCH, "max_position_embeddings": 40960})
    cfg = load_catalog(settings.models_file)[MODEL]
    metadata = context_configuration.model_metadata(cfg, tmp_path)
    presets = context_configuration.context_presets(cfg, metadata)
    assert presets[-1] == {**presets[-1], "tokens": 40960, "model_max": True, "trial": True}
    assert [row["tokens"] for row in presets if row["model_max"]] == [40960]
    no_metadata = _settings(tmp_path / "bare", metadata=None)
    bare = load_catalog(no_metadata.models_file)[MODEL]
    assert not any(row["model_max"] for row in context_configuration.context_presets(bare, {}))
