"""Hardware-aware precision policy for OpenVINO model preparation.

The policy is deliberately conservative. It chooses a *preparation preference*, not a
hardware certification or a quality claim. Real benchmark and quality evidence should
always win over these priors.
"""

from __future__ import annotations

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


def recommend_quantization(
    *,
    backend: str,
    device: str | None,
    profile: str | None = "balanced",
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
        return QuantizationRecommendation(
            "int4",
            "Intel NPU text-generation preparation prefers InferBridge's NPU-safe symmetric INT4 profile.",
            group_size=int(defaults["group_size"]),
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

    return {"fp16": 0.0, "int8": 1.5, "int4": 4.0}.get(
        str(weight_format or "fp16").lower(), 8.0
    )


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
