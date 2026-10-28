"""Conservative, model-specific context validation and memory guidance.

Model architecture and OpenVINO target capability are distinct. A valid model
position limit permits a *trial*, not a promise that a device can compile it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.model_registry import ModelConfig

MAX_CONTEXT = 262_144
MIN_CONTEXT = 128
# Common sizes offered as one-click presets in every editor (native and web).
STANDARD_PRESETS = (2048, 4096, 8192, 16384, 32768, 65536, 131072)
_METADATA_LIMIT = 4 * 1024 * 1024

# Per-model KV-cache precision. "auto" leaves OpenVINO's own selection in place
# (KV_CACHE_PRECISION is not sent). Explicit values are passed as the
# KV_CACHE_PRECISION plugin property to CPU and GPU LLM/VLM pipelines only; the
# NPU plugin compiles its own static KV layout and is never sent the property.
# See docs.openvino.ai "Managing iGPU Memory Allocation" (u8, u4, f16 on GPU) and
# the OpenVINO 2026.2 GPU INT4 KV-cache post.
KV_CACHE_PRECISIONS = ("auto", "f16", "u8", "u4")
# Estimated stored bytes per K/V element, including per-group scale/zero-point
# overhead. Ratios follow Intel's published Llama-3-8B measurements on GPU
# (FP16 1024 MB, INT8 584 MB, INT4 328 MB at 8K): about 0.57x and 0.32x of FP16.
# These are planning estimates, not measured device memory.
KV_BYTES_PER_ELEMENT = {"f16": 2.0, "u8": 1.125, "u4": 0.625}
KV_CACHE_PRECISION_LABELS = {
    "auto": "Auto (OpenVINO default)",
    "f16": "FP16 (no KV compression)",
    "u8": "INT8 (u8, ~half of FP16)",
    "u4": "INT4 (u4, ~a third of FP16)",
}


class ContextValidationError(ValueError):
    """A deterministic rejection whose message is safe to display to users."""


def model_metadata(cfg: ModelConfig, base_dir: Path) -> dict[str, Any]:
    path = cfg.abs_path(base_dir) / "config.json"
    try:
        if not path.is_file() or not 0 < path.stat().st_size <= _METADATA_LIMIT:
            return {}
        with path.open("rb") as stream:
            data = json.loads(stream.read(_METADATA_LIMIT + 1).decode("utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except (OSError, UnicodeError, ValueError):
        return {}


def _positive_int(value: Any) -> int | None:
    return value if type(value) is int and 0 < value <= MAX_CONTEXT else None


def model_limit(metadata: dict[str, Any]) -> int | None:
    """Only trust explicit architectural bounds; never infer from tokenizer marketing."""
    values = [
        _positive_int(metadata.get(key))
        for key in ("max_position_embeddings", "n_positions", "n_ctx")
    ]
    # Sliding-window attention cannot promise full-history attention past its window.
    values.append(_positive_int(metadata.get("sliding_window")))
    return min(value for value in values if value is not None) if any(values) else None


def validate_context(cfg: ModelConfig, requested: int, metadata: dict[str, Any]) -> None:
    if cfg.backend == "openvino-embeddings":
        raise ContextValidationError("Embedding models do not support chat context configuration.")
    if type(requested) is not int or not 128 <= requested <= MAX_CONTEXT:
        raise ContextValidationError("Context must be a whole number between 128 and 262144.")
    if requested <= cfg.max_output_tokens + 64:
        raise ContextValidationError(
            "Context must leave at least 64 prompt tokens beyond output reserve."
        )
    recommended = cfg.recommended_context_len or cfg.max_context_len
    limit = model_limit(metadata)
    if limit is not None and requested > limit:
        raise ContextValidationError(
            "Requested context exceeds the converted model's architecture limit."
        )
    if limit is None and requested > recommended:
        raise ContextValidationError(
            "Cannot increase context without verified local model architecture metadata."
        )


def normalize_kv_cache_precision(value: Any) -> str | None:
    """Return the stored form: ``None`` for Auto, else ``f16``/``u8``/``u4``.

    Raises :class:`ContextValidationError` for anything else so an unsupported
    value is refused with a friendly message before it reaches OpenVINO.
    """
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in ("", "auto", "default"):
        return None
    if text not in KV_BYTES_PER_ELEMENT:
        raise ContextValidationError("KV cache precision must be one of: Auto, f16, u8 or u4.")
    return text


def kv_precision_device_supported(device: str | None) -> bool:
    """True when *device* is one physical CPU or GPU (the plugins that accept it).

    NPU compiles a static KV layout and does not take KV_CACHE_PRECISION; AUTO and
    composite expressions may route to the NPU, so the property is not sent there.
    """
    text = str(device or "").strip().upper()
    if not text or ":" in text or "," in text:
        return False
    base = text.split(".", 1)[0]
    return base in ("CPU", "GPU")


def effective_kv_bytes(precision: str | None, device: str | None = None) -> float:
    """Bytes per element used for an estimate; FP16 when Auto, NPU or unknown.

    Auto is shown at the FP16 upper bound because OpenVINO chooses the effective
    precision per model and device (often u8 on CPU/GPU) and does not report it.
    """
    if device is not None and not kv_precision_device_supported(device):
        return KV_BYTES_PER_ELEMENT["f16"]
    return KV_BYTES_PER_ELEMENT.get(str(precision or "").lower(), KV_BYTES_PER_ELEMENT["f16"])


def kv_cache_gib(
    metadata: dict[str, Any], context: int, precision: str | None = "f16"
) -> float | None:
    """Estimated K+V allocation at *precision* (FP16 by default).

    Not measured runtime or NPU memory: f16 2 bytes, u8 ~1.125 and u4 ~0.625
    bytes per element including scale overhead. Auto/None estimates as FP16.
    """
    layers = _positive_int(metadata.get("num_hidden_layers"))
    heads = _positive_int(metadata.get("num_key_value_heads"))
    if heads is None:
        heads = _positive_int(metadata.get("num_attention_heads"))
    head_dim = _positive_int(metadata.get("head_dim"))
    if head_dim is None:
        hidden = _positive_int(metadata.get("hidden_size"))
        attention_heads = _positive_int(metadata.get("num_attention_heads"))
        if hidden and attention_heads and hidden % attention_heads == 0:
            head_dim = hidden // attention_heads
    if not all((layers, heads, head_dim)):
        return None
    element_bytes = effective_kv_bytes(precision)
    return round(context * 2 * layers * heads * head_dim * element_bytes / (1024**3), 3)


def _kv_estimates(
    cfg: ModelConfig, metadata: dict[str, Any], value: int, device: str | None
) -> dict[str, float | None]:
    precision = getattr(cfg, "kv_cache_precision", None)
    effective = precision if device is None or kv_precision_device_supported(device) else None
    return {
        "fp16_kv_cache_gib_estimate": kv_cache_gib(metadata, value),
        "kv_cache_gib_estimate": kv_cache_gib(metadata, value, effective),
    }


def context_options(
    cfg: ModelConfig, metadata: dict[str, Any], *, device: str | None = None
) -> list[dict[str, Any]]:
    recommended = cfg.recommended_context_len or cfg.max_context_len
    upper = min(model_limit(metadata) or recommended, MAX_CONTEXT)
    candidates = sorted(
        {
            cfg.max_context_len,
            recommended,
            upper,
            512,
            1024,
            2048,
            4096,
            8192,
            16384,
            32768,
            65536,
            131072,
            262144,
        }
    )
    options = []
    for value in candidates:
        try:
            validate_context(cfg, value, metadata)
        except ValueError:
            continue
        options.append(
            {
                "tokens": value,
                "classification": (
                    "recommended"
                    if value == recommended
                    else "lower-memory"
                    if value < recommended
                    else "eligible-unverified"
                ),
                **_kv_estimates(cfg, metadata, value, device),
            }
        )
    if cfg.backend != "openvino-embeddings" and not any(
        option["tokens"] == cfg.max_context_len for option in options
    ):
        # A legacy preference may exceed newly discovered architecture limits.
        # Show the actual applied value without offering it as a new safe choice.
        options.append(
            {
                "tokens": cfg.max_context_len,
                "classification": "existing-unverified",
                **_kv_estimates(cfg, metadata, cfg.max_context_len, device),
            }
        )
        options.sort(key=lambda option: option["tokens"])
    return options


def is_configurable(cfg: ModelConfig) -> bool:
    return cfg.backend != "openvino-embeddings"


def context_bounds(cfg: ModelConfig, metadata: dict[str, Any]) -> tuple[int, int]:
    """Inclusive custom-value bounds that :func:`validate_context` accepts."""
    recommended = cfg.recommended_context_len or cfg.max_context_len
    upper = min(model_limit(metadata) or recommended, MAX_CONTEXT)
    lower = max(MIN_CONTEXT, cfg.max_output_tokens + 65)
    return lower, upper


def context_presets(
    cfg: ModelConfig, metadata: dict[str, Any], *, device: str | None = None
) -> list[dict[str, Any]]:
    """Standard sizes plus "max for this model"; above-recommended sizes are trials."""
    if not is_configurable(cfg):
        return []
    recommended = cfg.recommended_context_len or cfg.max_context_len
    _, upper = context_bounds(cfg, metadata)
    verified_max = model_limit(metadata) is not None
    values = sorted(set(STANDARD_PRESETS) | ({upper} if verified_max else set()))
    presets = []
    for value in values:
        try:
            validate_context(cfg, value, metadata)
        except ContextValidationError:
            continue
        presets.append(
            {
                "tokens": value,
                "trial": value > recommended,
                "recommended": value == recommended,
                "model_max": verified_max and value == upper,
                **_kv_estimates(cfg, metadata, value, device),
            }
        )
    return presets


def kv_cache_precision_options(device: str | None) -> list[dict[str, Any]]:
    """Choices for every editor; explicit values are unavailable on NPU/AUTO loads."""
    supported = device is None or kv_precision_device_supported(device)
    rows = []
    for value in KV_CACHE_PRECISIONS:
        rows.append(
            {
                "value": value,
                "label": KV_CACHE_PRECISION_LABELS[value],
                "available": value == "auto" or supported,
                "bytes_per_element_estimate": KV_BYTES_PER_ELEMENT.get(value),
            }
        )
    return rows


def _is_npu(device: str | None) -> bool:
    return str(device or "").strip().upper().split(".", 1)[0] == "NPU"


def context_guidance(cfg: ModelConfig, metadata: dict[str, Any], device: str | None) -> str:
    """Plain-language reason the defaults are small and how far CPU/GPU can go."""
    recommended = cfg.recommended_context_len or cfg.max_context_len
    _, upper = context_bounds(cfg, metadata)
    limit = model_limit(metadata)
    npu = (
        f"The {recommended:,}-token recommendation is an NPU-safe default: the NPU compiles "
        f"a static graph with MAX_PROMPT_LEN = context minus the {cfg.max_output_tokens:,}-token "
        "output reserve and MIN_RESPONSE_LEN = the reserve, so larger values recompile and "
        "use more NPU memory."
    )
    if limit is None:
        return (
            npu + " This model's config.json does not state an architecture limit, so the "
            "editor cannot go above the recommendation until metadata is available."
        )
    cpu_gpu = (
        f" CPU and GPU use dynamic shapes and can go up to this model's maximum of "
        f"{upper:,} tokens; KV-cache memory grows linearly with context, and INT8/INT4 "
        "KV precision reduces it."
    )
    if _is_npu(device):
        return npu + cpu_gpu.replace(" CPU and GPU", " On CPU or GPU the same model")
    return npu + cpu_gpu


def context_summary(cfg: ModelConfig, base_dir: Path) -> dict[str, Any]:
    """Cheap per-model context facts for model lists; never touches an engine."""
    metadata = model_metadata(cfg, base_dir)
    configurable = is_configurable(cfg)
    recommended = cfg.recommended_context_len or cfg.max_context_len
    lower, upper = context_bounds(cfg, metadata)
    return {
        "model_id": cfg.id,
        "name": cfg.name,
        "backend": cfg.backend,
        "configurable": configurable,
        "configured_context_tokens": cfg.max_context_len if configurable else None,
        "recommended_context_tokens": recommended if configurable else None,
        "reserved_output_tokens": cfg.max_output_tokens if configurable else None,
        "model_architecture_limit_tokens": model_limit(metadata) if configurable else None,
        "minimum_context_tokens": lower if configurable else None,
        "maximum_context_tokens": upper if configurable else None,
        "trial": configurable and cfg.max_context_len > recommended,
        "kv_cache_precision": (getattr(cfg, "kv_cache_precision", None) or "auto")
        if configurable
        else None,
    }


def describe_context(
    cfg: ModelConfig, base_dir: Path, *, device: str | None = None, mock: bool = False
) -> dict:
    metadata = model_metadata(cfg, base_dir)
    mock = mock or device == "MOCK"
    # The loaded engine's device decides where KV precision applies; None = unloaded.
    live = None if device in (None, "MOCK") else device
    precision = getattr(cfg, "kv_cache_precision", None) or "auto"
    precision_applies = live is None or kv_precision_device_supported(live)
    recommended = cfg.recommended_context_len or cfg.max_context_len
    limit = model_limit(metadata)
    lower, upper = context_bounds(cfg, metadata)
    return {
        "model_id": cfg.id,
        "name": cfg.name,
        "configurable": is_configurable(cfg),
        "minimum_context_tokens": lower,
        "maximum_context_tokens": upper,
        "custom_values_allowed": is_configurable(cfg),
        "presets": context_presets(cfg, metadata, device=live),
        "model_max_context_tokens": upper if limit else None,
        "configured_context_tokens": cfg.max_context_len,
        "recommended_context_tokens": recommended,
        "prompt_budget_tokens": cfg.max_prompt_len,
        "reserved_output_tokens": cfg.max_output_tokens,
        "model_architecture_limit_tokens": limit,
        "architecture_evidence": "local config.json" if limit else "unavailable",
        "runtime_status": "mock-unverified" if mock else "hardware-unverified",
        "options": context_options(cfg, metadata, device=live),
        "kv_cache_precision": precision,
        "kv_cache_precision_options": kv_cache_precision_options(live),
        "kv_cache_precision_applies": precision_applies,
        "kv_cache_precision_note": (
            "KV cache precision applies to CPU and GPU loads. Auto keeps OpenVINO's own "
            "choice (often INT8 on CPU/GPU); estimates for Auto use the FP16 upper bound."
            if precision_applies
            else "The NPU manages its own KV cache, so KV cache precision is unavailable "
            "there. The saved value is used when this model loads on CPU or GPU."
        ),
        "context_guidance": context_guidance(cfg, metadata, live),
        "explanation": (
            "Context values above the recommendation are trials, not validated hardware limits. "
            "Applying a change rebuilds the OpenVINO pipeline; available RAM and successful "
            "compilation do not guarantee long-prompt generation. KV-cache figures are "
            "architectural estimates (FP16 2 bytes, INT8 ~1.1, INT4 ~0.6 bytes per value "
            "including scales), not measured device memory."
        ),
    }
