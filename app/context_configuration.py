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
_METADATA_LIMIT = 4 * 1024 * 1024


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


def kv_cache_gib(metadata: dict[str, Any], context: int) -> float | None:
    """Reference FP16 K+V allocation, not measured runtime or NPU memory."""
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
    return round(context * 2 * layers * heads * head_dim * 2 / (1024**3), 3)


def context_options(cfg: ModelConfig, metadata: dict[str, Any]) -> list[dict[str, Any]]:
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
                "fp16_kv_cache_gib_estimate": kv_cache_gib(metadata, value),
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
                "fp16_kv_cache_gib_estimate": kv_cache_gib(metadata, cfg.max_context_len),
            }
        )
        options.sort(key=lambda option: option["tokens"])
    return options


def describe_context(cfg: ModelConfig, base_dir: Path, *, device: str | None = None) -> dict:
    metadata = model_metadata(cfg, base_dir)
    recommended = cfg.recommended_context_len or cfg.max_context_len
    limit = model_limit(metadata)
    return {
        "model_id": cfg.id,
        "configured_context_tokens": cfg.max_context_len,
        "recommended_context_tokens": recommended,
        "prompt_budget_tokens": cfg.max_prompt_len,
        "reserved_output_tokens": cfg.max_output_tokens,
        "model_architecture_limit_tokens": limit,
        "architecture_evidence": "local config.json" if limit else "unavailable",
        "runtime_status": "mock-unverified" if device == "MOCK" else "hardware-unverified",
        "options": context_options(cfg, metadata),
        "explanation": (
            "Context values above the recommendation are trials, not validated hardware limits. "
            "NPU configuration rebuilds the OpenVINO pipeline; available RAM and successful "
            "compilation do not guarantee long-prompt generation. FP16 KV estimates, where "
            "shown, are architectural illustrations, not measured device memory."
        ),
    }
