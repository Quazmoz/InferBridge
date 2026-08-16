import json

from app.config import Settings
from app.model_library import ModelLibraryService
from app.model_manager import ModelManager


def _service(tmp_path):
    source_model = "example/model-1.5b"
    catalog = {
        "example-fp16": {
            "name": "Example FP16",
            "backend": "openvino-genai",
            "model_path": "models/openvino/example-fp16",
            "source_model": source_model,
            "weight_format": "fp16",
            "recommended_device": "CPU",
            "max_context_len": 2048,
            "max_output_tokens": 512,
        },
        "example-int8": {
            "name": "Example INT8",
            "backend": "openvino-genai",
            "model_path": "models/openvino/example-int8",
            "source_model": source_model,
            "weight_format": "int8",
            "recommended_device": "CPU",
            "max_context_len": 2048,
            "max_output_tokens": 512,
        },
        "example-int4": {
            "name": "Example INT4",
            "backend": "openvino-genai",
            "model_path": "models/openvino/example-int4",
            "source_model": source_model,
            "weight_format": "int4",
            "recommended_device": "CPU",
            "max_context_len": 2048,
            "max_output_tokens": 512,
        },
    }
    models_file = tmp_path / "models.json"
    models_file.write_text(json.dumps(catalog, indent=2) + "\n", encoding="utf-8")
    settings = Settings(
        models_file=models_file,
        models_dir=tmp_path / "models",
        cache_dir=tmp_path / "cache",
        benchmark_results_file=tmp_path / "benchmarks.json",
        force_mock=True,
        device="CPU",
    )
    manager = ModelManager(settings)
    manager.advisor.recommend_device = lambda cfg, **kwargs: "CPU"
    return ModelLibraryService(settings, manager)


def _item(snapshot, model_id):
    return next(item for item in snapshot["items"] if item["id"] == model_id)


def test_balanced_policy_points_fp16_card_to_int8_sibling_without_requantizing_it(tmp_path):
    service = _service(tmp_path)

    item = _item(service.snapshot(profile="balanced", include_all=True), "example-fp16")
    recommendation = item["recommended_quantization"]

    assert recommendation["format"] == "fp16"
    assert recommendation["preferred_format"] == "int8"
    assert recommendation["preferred_model_id"] == "example-int8"
    assert recommendation["preferred_device"] == "CPU"
    assert "remains FP16" in recommendation["reason"]


def test_memory_policy_points_fp16_card_to_int4_sibling(tmp_path):
    service = _service(tmp_path)

    item = _item(service.snapshot(profile="lowest_memory", include_all=True), "example-fp16")
    recommendation = item["recommended_quantization"]

    assert recommendation["format"] == "fp16"
    assert recommendation["preferred_format"] == "int4"
    assert recommendation["preferred_model_id"] == "example-int4"
    assert recommendation["group_size"] == 128
    assert recommendation["ratio"] == 1.0
    assert recommendation["sym"] is True


def test_best_quality_policy_points_int4_card_to_fp16_sibling(tmp_path):
    service = _service(tmp_path)

    item = _item(service.snapshot(profile="best_quality", include_all=True), "example-int4")
    recommendation = item["recommended_quantization"]

    assert recommendation["format"] == "int4"
    assert recommendation["preferred_format"] == "fp16"
    assert recommendation["preferred_model_id"] == "example-fp16"


def test_missing_preferred_sibling_never_changes_current_precision_identity(tmp_path):
    service = _service(tmp_path)
    del service.manager.catalog["example-int8"]

    item = _item(service.snapshot(profile="balanced", include_all=True), "example-fp16")
    recommendation = item["recommended_quantization"]

    assert recommendation["format"] == "fp16"
    assert recommendation["preferred_format"] == "int8"
    assert recommendation["preferred_model_id"] is None
    assert "stays unchanged" in recommendation["reason"]
