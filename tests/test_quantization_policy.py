from app.quantization_policy import (
    estimated_quality_penalty,
    int4_conversion_defaults,
    profile_precision_bonus,
    recommend_quantization,
)


def test_npu_generation_prefers_safe_int4_profile():
    recommendation = recommend_quantization(
        backend="openvino-genai", device="NPU", profile="balanced"
    )

    assert recommendation.weight_format == "int4"
    assert recommendation.group_size == 128
    assert recommendation.ratio == 1.0
    assert recommendation.sym is True
    assert int4_conversion_defaults() == {"group_size": 128, "ratio": 1.0, "sym": True}


def test_balanced_cpu_and_gpu_prefer_int8_middle_ground():
    assert (
        recommend_quantization(
            backend="openvino-genai", device="CPU", profile="balanced"
        ).weight_format
        == "int8"
    )
    assert (
        recommend_quantization(
            backend="openvino-genai", device="GPU", profile="balanced"
        ).weight_format
        == "int8"
    )


def test_memory_speed_and_power_profiles_prefer_int4():
    for profile in ("fastest", "lowest-memory", "lowest-power"):
        assert (
            recommend_quantization(
                backend="openvino-genai", device="GPU", profile=profile
            ).weight_format
            == "int4"
        )


def test_best_quality_and_quality_sensitive_backends_keep_fp16():
    assert (
        recommend_quantization(
            backend="openvino-genai", device="GPU", profile="best-quality"
        ).weight_format
        == "fp16"
    )
    assert (
        recommend_quantization(
            backend="openvino-embeddings", device="CPU", profile="lowest-memory"
        ).weight_format
        == "fp16"
    )
    assert (
        recommend_quantization(
            backend="openvino-vlm", device="GPU", profile="fastest"
        ).weight_format
        == "fp16"
    )


def test_quality_prior_does_not_treat_compression_as_lossless():
    assert estimated_quality_penalty("fp16") < estimated_quality_penalty("int8")
    assert estimated_quality_penalty("int8") < estimated_quality_penalty("int4")


def test_profile_precision_prior_matches_policy():
    assert profile_precision_bonus("int4", "lowest-memory", device="GPU") > profile_precision_bonus(
        "int8", "lowest-memory", device="GPU"
    )
    assert profile_precision_bonus("int8", "balanced", device="GPU") > profile_precision_bonus(
        "fp16", "balanced", device="GPU"
    )
    assert profile_precision_bonus("fp16", "best-quality", device="GPU") > profile_precision_bonus(
        "int4", "best-quality", device="GPU"
    )
    assert profile_precision_bonus("int4", "balanced", device="NPU") > profile_precision_bonus(
        "int8", "balanced", device="NPU"
    )


def test_bundled_catalog_keeps_truthful_separate_precision_variants():
    import json
    from pathlib import Path

    catalog = json.loads((Path(__file__).resolve().parents[1] / "models.json").read_text())
    for family in ("qwen2.5-1.5b", "qwen2.5-3b"):
        int4 = catalog[f"{family}-int4"]
        int8 = catalog[f"{family}-int8"]
        fp16 = catalog[f"{family}-fp16"]
        assert {int4["weight_format"], int8["weight_format"], fp16["weight_format"]} == {
            "int4",
            "int8",
            "fp16",
        }
        assert len({int4["model_path"], int8["model_path"], fp16["model_path"]}) == 3
        assert int4["source_model"] == int8["source_model"] == fp16["source_model"]


def test_int8_catalog_candidates_do_not_default_to_npu():
    import json
    from pathlib import Path

    catalog = json.loads((Path(__file__).resolve().parents[1] / "models.json").read_text())
    int8_entries = [entry for entry in catalog.values() if entry["weight_format"] == "int8"]
    assert int8_entries
    assert all(entry["recommended_device"] in {"CPU", "GPU"} for entry in int8_entries)
