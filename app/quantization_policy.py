"""Hardware-aware precision policy for OpenVINO model preparation.

The policy is deliberately conservative. It chooses a *preparation preference*, not a
hardware certification or a quality claim. Real benchmark and quality evidence should
always win over these priors.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

SUPPORTED_WEIGHT_FORMATS = frozenset({"int4", "int8", "fp16"})
_GENERATION_BACKENDS = frozenset({"openvino-genai"})
_QUALITY_SENSITIVE_BACKENDS = frozenset({"openvino-embeddings", "openvino-vlm"})
_PROFILE_ALIASES = {
    "default": "balanced",
    "fast": "fastest",
    "speed": "fastest",
    "quality": "best-quality",
    "best": "best-quality",
    "best_quality": "best-quality",
    "memory": "lowest-memory",
    "low-memory": "lowest-memory",
    "lowest_memory": "lowest-memory",
    "power": "lowest-power",
    "low-power": "lowest-power",
    "lowest_power": "lowest-power",
}
_SUPPORTED_PROFILES = frozenset(
    {"fastest", "balanced", "best-quality", "lowest-memory", "lowest-power"}
)


@dataclass(frozen=True)
class QuantizationRecommendation:
    """A precision preference plus safe conversion defaults when applicable."""

    weight_format: str
    reason: str
    group_size: int | None = None
    ratio: float | None = None
    sym: bool | None = None


def _normalize_profile(profile: str | None) -> str:
    value = str(profile or "balanced").strip().lower().replace(" ", "-")
    value = _PROFILE_ALIASES.get(value, value)
    if value not in _SUPPORTED_PROFILES:
        return "balanced"
    return value


def _base_device(device: str | None) -> str:
    text = str(device or "CPU").strip().upper()
    if ":" in text:
        text = text.split(":", 1)[1].split(",", 1)[0]
    return text.split(".", 1)[0] or "CPU"


def int4_conversion_defaults() -> dict[str, object]:
    """Return the portable INT4 profile used by InferBridge's NPU safety layer."""

    return {"group_size": 128, "ratio": 1.0, "sym": True}


# Intel's NPU guidance (docs.openvino.ai, "OpenVINO GenAI on NPU") allows INT4 symmetric,
# ratio 1.0 with group size 128 (GQ, recommended up to ~4-5B parameters) or channel-wise
# -1 (CW, better for larger models). OVMS deploys NPU LLMs from -cw-ov repositories.
# Phi-4-mini (3.8B, 200k vocabulary, LongRoPE) has failed NPU compilation with GQ128
# ("Failed to compile. No more devices are left!" on NPU driver 32.0.100.3967,
# openvino.genai#2082, where a newer driver fixed it for the reporter) and on Meteor
# Lake (NPU 3720, InferBridge #65, ZE_RESULT_ERROR_INVALID_ARGUMENT). A community
# conversion reported as working on NPU in #2082 ("with help from Intel folks",
# huggingface.co/FluidInference/phi-4-mini-instruct-int4-ov-npu) uses
# `--weight-format int4 --group-size -1 --ratio 1.0 --sym`. No public
# OpenVINO/Phi-4-mini-instruct-int4-cw-ov repository exists to download.
NPU_CHANNEL_WISE_SOURCES = frozenset(
    {"microsoft/phi-4-mini-instruct", "microsoft/phi-4-mini-reasoning"}
)
NPU_GROUP_WISE_MAX_PARAMS_B = 4.0
NPU_3720_GROUP_WISE_MAX_PARAMS_B = 3.0
NPU_KNOWN_ISSUES = {
    "microsoft/phi-4-mini-instruct": (
        "NPU is unverified for Phi-4 Mini: older NPU drivers and Meteor Lake (NPU 3720) "
        "can reject its NPU compilation (ZE_RESULT_ERROR_INVALID_ARGUMENT). Use the "
        "latest Intel NPU driver and convert it for NPU with channel-wise INT4 "
        "(group size -1), or load it on GPU."
    ),
}
_PARAMS_RE = re.compile(r"(?<![A-Za-z0-9.])(\d+(?:\.\d+)?)\s*[bB](?![A-Za-z])")


def _source_key(source_model: str | None) -> str:
    return str(source_model or "").strip().lower()


def estimated_parameters_b(source_model: str | None) -> float | None:
    """Parameter count in billions parsed from a repository name ("Qwen2.5-7B" -> 7.0).

    ponytail: name heuristic only; MoE names ("30B-A3B") report the total. Unknown
    names (Phi-4-mini) return None and rely on the explicit source list.
    """
    name = _source_key(source_model).rsplit("/", 1)[-1]
    match = _PARAMS_RE.search(name)
    return float(match.group(1)) if match else None


def npu_int4_group_size(source_model: str | None, *, npu_architecture: str | None = None) -> int:
    """INT4 group size for an NPU-targeted conversion: -1 (channel-wise) or 128.

    Channel-wise for sources known to need it, for models above ~4B parameters (Intel's
    GQ128 guidance), and for 3B+ models on NPU 3720 (Meteor Lake/Arrow Lake).
    """
    if _source_key(source_model) in NPU_CHANNEL_WISE_SOURCES:
        return -1
    params = estimated_parameters_b(source_model)
    if params is not None and params > NPU_GROUP_WISE_MAX_PARAMS_B:
        return -1
    arch = str(npu_architecture or "").strip()
    if params is not None and "3720" in arch and params >= NPU_3720_GROUP_WISE_MAX_PARAMS_B:
        return -1
    return 128


def npu_known_issue(source_model: str | None) -> str | None:
    """A user-facing NPU caveat for sources with a known NPU problem, else None."""
    return NPU_KNOWN_ISSUES.get(_source_key(source_model))


def recommend_quantization(
    *,
    backend: str,
    device: str | None,
    profile: str | None = "balanced",
    source_model: str | None = None,
    npu_architecture: str | None = None,
) -> QuantizationRecommendation:
    """Recommend a weight format without claiming device certification.

    NPU text-generation preparation prefers symmetric INT4. CPU/GPU balanced use
    prefers INT8 as the conservative compressed middle ground. Quality-sensitive
    backends and the explicit best-quality profile retain FP16 unless the user has
    model-specific evidence supporting a compressed alternative.
    """

    normalized_backend = str(backend or "openvino-genai").strip().lower()
    normalized_profile = _normalize_profile(profile)
    base = _base_device(device)

    if normalized_backend in _QUALITY_SENSITIVE_BACKENDS:
        return QuantizationRecommendation(
            "fp16",
            "Keep FP16 for this backend until model-specific quality validation supports compression.",
        )
    if normalized_backend not in _GENERATION_BACKENDS:
        return QuantizationRecommendation(
            "fp16", "Unknown backend: preserve FP16 for compatibility."
        )
    if normalized_profile == "best-quality":
        return QuantizationRecommendation(
            "fp16", "Best-quality mode preserves FP16 as the fidelity-first fallback."
        )
    if base == "NPU":
        defaults = int4_conversion_defaults()
        group_size = npu_int4_group_size(source_model, npu_architecture=npu_architecture)
        return QuantizationRecommendation(
            "int4",
            "Intel NPU text-generation preparation prefers InferBridge's NPU-safe symmetric INT4 profile"
            + (" with channel-wise quantization (group size -1)." if group_size == -1 else "."),
            group_size=group_size,
            ratio=float(defaults["ratio"]),
            sym=bool(defaults["sym"]),
        )
    if normalized_profile in {"fastest", "lowest-memory", "lowest-power"}:
        defaults = int4_conversion_defaults()
        return QuantizationRecommendation(
            "int4",
            "This profile prioritizes the smallest weight footprint; benchmark locally before making throughput claims.",
            group_size=int(defaults["group_size"]),
            ratio=float(defaults["ratio"]),
            sym=bool(defaults["sym"]),
        )
    return QuantizationRecommendation(
        "int8",
        "Balanced CPU/GPU preparation prefers INT8 as a conservative compressed middle ground.",
    )


def estimated_quality_penalty(weight_format: str) -> float:
    """Return a small heuristic penalty used only for advisor ranking.

    This is intentionally not presented as measured quality. It prevents compressed
    variants from being treated as mathematically identical to FP16 before evidence
    exists while still allowing measured performance and memory fit to influence the
    recommendation.
    """

    return {"fp16": 0.0, "int8": 1.5, "int4": 4.0}.get(str(weight_format or "fp16").lower(), 8.0)


def profile_precision_bonus(
    weight_format: str,
    profile: str | None,
    *,
    device: str | None = None,
) -> float:
    """Return a bounded policy prior for advisor ranking.

    The bonus is deliberately modest so local benchmark evidence and compatibility
    can outweigh it. Best-quality favors FP16, balanced favors INT8 on CPU/GPU,
    and memory/power/speed profiles favor INT4. NPU receives an additional INT4
    preference and discourages INT8 as a default NPU path.
    """

    precision = str(weight_format or "fp16").lower()
    normalized_profile = _normalize_profile(profile)
    base = _base_device(device)

    by_profile = {
        "fastest": {"int4": 6.0, "int8": 3.0, "fp16": 0.0},
        "balanced": {"int4": 2.5, "int8": 5.0, "fp16": 0.0},
        "best-quality": {"int4": 0.0, "int8": 4.0, "fp16": 9.0},
        "lowest-memory": {"int4": 12.0, "int8": 6.0, "fp16": 0.0},
        "lowest-power": {"int4": 9.0, "int8": 4.0, "fp16": 0.0},
    }
    bonus = by_profile[normalized_profile].get(precision, -4.0)
    if base == "NPU":
        if precision == "int4":
            bonus += 6.0
        elif precision == "int8":
            bonus -= 8.0
    return bonus
