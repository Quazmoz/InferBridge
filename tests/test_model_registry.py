import json

from app.config import BASE_DIR
from app.model_registry import (
    is_downloaded,
    load_catalog,
    make_catalog_entry,
)
from runtime.device_check import parse_device_expression


def _write_catalog(tmp_path, data):
    path = tmp_path / "models.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_load_catalog_parses_entries(tmp_path):
    path = _write_catalog(
        tmp_path,
        {
            "m1": {
                "name": "Model One",
                "model_path": "models/openvino/m1",
                "source_model": "org/model-one",
                "max_context_len": 4096,
                "max_output_tokens": 1024,
                "trust_remote_code": True,
            }
        },
    )
    catalog = load_catalog(path)
    assert "m1" in catalog
    cfg = catalog["m1"]
    assert cfg.name == "Model One"
    assert cfg.max_prompt_len == 4096 - 1024
    assert cfg.recommended_device == "NPU"
    assert cfg.trust_remote_code is True


def test_shipped_catalog_uses_supported_formats_and_recommended_devices():
    catalog = load_catalog(BASE_DIR / "models.json")
    assert catalog
    assert all(cfg.weight_format in {"fp16", "int4", "int8"} for cfg in catalog.values())
    assert catalog["tinyllama-1.1b-chat-int4"].weight_format == "int4"
    assert catalog["qwen2.5-1.5b-int8"].weight_format == "int8"
    assert all(parse_device_expression(cfg.recommended_device) for cfg in catalog.values())


def test_load_catalog_missing_file_returns_empty(tmp_path):
    assert load_catalog(tmp_path / "nope.json") == {}


def test_load_catalog_rejects_non_boolean_remote_code_policy(tmp_path):
    path = _write_catalog(
        tmp_path,
        {
            "safe": {"name": "Safe", "trust_remote_code": False},
            "ambiguous": {"name": "Ambiguous", "trust_remote_code": "false"},
        },
    )
    catalog = load_catalog(path)
    assert catalog["safe"].trust_remote_code is False
    assert "ambiguous" not in catalog


def test_load_catalog_skips_malformed_entries(tmp_path):
    path = _write_catalog(tmp_path, {"good": {"name": "G"}, "bad": "not-an-object"})
    catalog = load_catalog(path)
    assert "good" in catalog
    assert "bad" not in catalog


def test_load_catalog_rejects_invalid_runtime_contracts(tmp_path):
    path = _write_catalog(
        tmp_path,
        {
            "valid-embedding": {
                "name": "Embedding",
                "backend": "openvino-embeddings",
                "weight_format": "fp16",
                "recommended_device": "CPU",
                "max_context_len": 512,
                "max_output_tokens": 0,
            },
            "bad_backend": {"name": "Bad", "backend": None},
            "bad-format": {"name": "Bad", "weight_format": "gguf"},
            "bad-device": {"name": "Bad", "recommended_device": "CUDA"},
            "bad-context": {"name": "Bad", "max_context_len": -1},
            "bad-output": {
                "name": "Bad",
                "max_context_len": 512,
                "max_output_tokens": 512,
            },
            "bad-embedding-output": {
                "name": "Bad",
                "backend": "openvino-embeddings",
                "max_output_tokens": 1,
            },
            "bad-name": {"name": ["not", "text"]},
            "bad/id": {"name": "Unsafe ID"},
        },
    )

    catalog = load_catalog(path)

    assert set(catalog) == {"valid-embedding"}
    assert catalog["valid-embedding"].max_prompt_len == 512


def test_is_downloaded_requires_complete_ir_artifacts(tmp_path):
    path = _write_catalog(tmp_path, {"m1": {"name": "M1", "model_path": "ir/m1"}})
    cfg = load_catalog(path)["m1"]
    assert not is_downloaded(cfg, tmp_path)

    model_dir = tmp_path / "ir" / "m1"
    model_dir.mkdir(parents=True)
    (model_dir / "openvino_model.xml").write_text(
        "<net name='model' version='11'></net>",
        encoding="utf-8",
    )
    assert not is_downloaded(cfg, tmp_path)

    (model_dir / "openvino_model.bin").write_bytes(b"weights")
    (model_dir / "config.json").write_text("{}", encoding="utf-8")
    assert is_downloaded(cfg, tmp_path)


def test_make_catalog_entry_status_precedence(tmp_path):
    path = _write_catalog(tmp_path, {"m1": {"name": "M1", "source_model": "org/model-one"}})
    cfg = load_catalog(path)["m1"]

    loaded = make_catalog_entry(cfg, loaded=True, queued=False, loading=False, downloaded=True)
    assert loaded["status"] == "loaded"
    assert loaded["can_unload"] is True
    assert loaded["can_load"] is False

    error = make_catalog_entry(
        cfg, loaded=False, queued=False, loading=False, downloaded=True, error="boom"
    )
    assert error["status"] == "error"
    assert error["status_label"] == "Load failed"
    assert error["error"] == "boom"

    conversion_error = make_catalog_entry(
        cfg, loaded=False, queued=False, loading=False, downloaded=False, error="boom"
    )
    assert conversion_error["status"] == "error"
    assert conversion_error["status_label"] == "Conversion failed"

    ready = make_catalog_entry(cfg, loaded=False, queued=False, loading=False, downloaded=True)
    assert ready["status"] == "ready_to_load"
    assert ready["can_load"] is True
    assert ready["can_convert"] is False
    assert ready["can_delete"] is True

    missing = make_catalog_entry(cfg, loaded=False, queued=False, loading=False, downloaded=False)
    assert missing["status"] == "not_downloaded"
    assert missing["can_load"] is False
    assert missing["can_convert"] is True
    assert missing["can_delete"] is False

    converting = make_catalog_entry(
        cfg, loaded=False, queued=False, loading=False, converting=True, downloaded=False
    )
    assert converting["status"] == "converting"
    assert converting["can_load"] is False
    assert converting["can_convert"] is False

    malformed_progress = make_catalog_entry(
        cfg,
        loaded=False,
        queued=False,
        loading=False,
        converting=True,
        downloaded=False,
        progress={"phase": "converting", "message": "Working", "percent": "unknown"},
    )
    assert malformed_progress["status"] == "converting"
    assert malformed_progress["status_label"] == "Working"

    no_source_path = _write_catalog(tmp_path, {"m2": {"name": "M2"}})
    no_source_cfg = load_catalog(no_source_path)["m2"]
    no_source = make_catalog_entry(
        no_source_cfg, loaded=False, queued=False, loading=False, downloaded=False
    )
    assert no_source["can_convert"] is False
