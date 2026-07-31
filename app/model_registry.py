"""Model catalog loading, validation, and UI-facing status entries.

This module is intentionally free of OpenVINO runtime imports. It describes which
models exist, where their OpenVINO IR directories live, and which API capabilities
their configured backend provides.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from app import multimodal
from runtime.device_check import DeviceValidationError, parse_device_expression

logger = logging.getLogger("ov-llm.registry")

# Files that indicate a directory holds a converted OpenVINO IR model. A generic
# config.json alone is not enough because Hugging Face caches contain one too.
_IR_MARKERS = ("openvino_model.xml", "openvino_language_model.xml")
_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_SUPPORTED_BACKENDS = frozenset(
    {"openvino-genai", "openvino-embeddings", "openvino-vlm"}
)
_SUPPORTED_WEIGHT_FORMATS = frozenset({"int4", "int8", "fp16"})
_MAX_CONTEXT_LEN = 262_144
_MAX_OUTPUT_TOKENS = 65_536

_STATUS_LABELS = {
    "loaded": "Loaded",
    "queued": "Queued…",
    "loading": "Loading…",
    "queued_convert": "Queued conversion…",
    "converting": "Converting…",
    "ready_to_load": "Ready to load",
    "not_downloaded": "Not converted",
    "cancelled": "Conversion cancelled",
    "error": "Load failed",
}

_PROGRESS_PHASE_LABELS = {
    "queued": "Queued",
    "downloading": "Downloading",
    "converting": "Converting",
    "loading": "Loading",
    "ready": "Ready",
    "error": "Error",
    "cancelled": "Cancelled",
}

_PROGRESS_PHASE_ICONS = {
    "queued": "⏳",
    "downloading": "⬇",
    "converting": "⚙",
    "loading": "🧠",
    "ready": "✓",
    "error": "⚠",
    "cancelled": "■",
}


@dataclass(frozen=True)
class ModelConfig:
    """A single entry from ``models.json`` with resolved, validated fields."""

    id: str
    name: str
    description: str
    backend: str
    model_path: str
    source_model: str
    weight_format: str
    recommended_device: str
    max_context_len: int
    max_output_tokens: int
    trust_remote_code: bool = False

    @property
    def max_prompt_len(self) -> int:
        """Token budget for the prompt, reserving room for the response."""

        return max(self.max_context_len - self.max_output_tokens, 64)

    @property
    def capabilities(self) -> tuple[str, ...]:
        return multimodal.capabilities_for_backend(self.backend)

    @property
    def supports_vision(self) -> bool:
        return multimodal.backend_supports_vision(self.backend)

    def abs_path(self, base_dir: Path) -> Path:
        """Absolute path to this model's OpenVINO IR directory."""

        path = Path(self.model_path)
        return path if path.is_absolute() else (base_dir / path)


def _string_field(
    raw: dict,
    key: str,
    default: str,
    *,
    allow_empty: bool = False,
) -> str:
    value = raw.get(key, default)
    if value is None and allow_empty:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a JSON string")
    value = value.strip()
    if not value and not allow_empty:
        raise ValueError(f"{key} cannot be empty")
    return value


def _integer_field(
    raw: dict,
    key: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be a JSON integer")
    if value < minimum or value > maximum:
        raise ValueError(f"{key} must be between {minimum} and {maximum}")
    return value


def _coerce_entry(model_id: str, raw: dict) -> ModelConfig:
    if not isinstance(model_id, str) or not _MODEL_ID_RE.fullmatch(model_id):
        raise ValueError("model id must be filesystem-safe and at most 128 characters")

    trust_remote_code = raw.get("trust_remote_code", False)
    if not isinstance(trust_remote_code, bool):
        raise ValueError("trust_remote_code must be a JSON boolean")

    backend = _string_field(raw, "backend", "openvino-genai").lower()
    if backend not in _SUPPORTED_BACKENDS:
        supported = ", ".join(sorted(_SUPPORTED_BACKENDS))
        raise ValueError(f"backend must be one of: {supported}")

    weight_format = _string_field(raw, "weight_format", "int4").lower()
    if weight_format not in _SUPPORTED_WEIGHT_FORMATS:
        supported = ", ".join(sorted(_SUPPORTED_WEIGHT_FORMATS))
        raise ValueError(f"weight_format must be one of: {supported}")

    recommended_device = _string_field(raw, "recommended_device", "NPU")
    try:
        recommended_device = parse_device_expression(recommended_device).normalized
    except DeviceValidationError as exc:
        raise ValueError(f"recommended_device is invalid: {exc}") from exc

    max_context_len = _integer_field(
        raw,
        "max_context_len",
        2048,
        minimum=128,
        maximum=_MAX_CONTEXT_LEN,
    )
    max_output_tokens = _integer_field(
        raw,
        "max_output_tokens",
        512,
        minimum=0,
        maximum=_MAX_OUTPUT_TOKENS,
    )
    if backend == "openvino-embeddings":
        if max_output_tokens != 0:
            raise ValueError("embedding models must use max_output_tokens=0")
    elif max_output_tokens < 1 or max_output_tokens >= max_context_len:
        raise ValueError(
            "generation models require max_output_tokens between 1 and max_context_len - 1"
        )

    return ModelConfig(
        id=model_id,
        name=_string_field(raw, "name", model_id),
        description=_string_field(raw, "description", "", allow_empty=True),
        backend=backend,
        model_path=_string_field(raw, "model_path", f"models/openvino/{model_id}"),
        source_model=_string_field(raw, "source_model", "", allow_empty=True),
        weight_format=weight_format,
        recommended_device=recommended_device,
        max_context_len=max_context_len,
        max_output_tokens=max_output_tokens,
        trust_remote_code=trust_remote_code,
    )


def load_catalog(models_file: Path) -> dict[str, ModelConfig]:
    """Load and validate the catalog. Return an empty mapping on invalid input."""

    path = Path(models_file)
    if not path.exists():
        logger.warning("Model catalog not found: %s", path)
        return {}

    try:
        with open(path, encoding="utf-8-sig") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("Failed to read model catalog %s: %s", path, exc)
        return {}

    if not isinstance(data, dict):
        logger.error("Model catalog %s must be a JSON object of {id: config}", path)
        return {}

    catalog: dict[str, ModelConfig] = {}
    for model_id, raw in data.items():
        if not isinstance(raw, dict):
            logger.warning("Skipping malformed catalog entry %r (not an object)", model_id)
            continue
        try:
            catalog[model_id] = _coerce_entry(model_id, raw)
        except (TypeError, ValueError) as exc:
            logger.warning("Skipping invalid catalog entry %r: %s", model_id, exc)
    return catalog


def is_openvino_model_dir(model_dir: Path) -> bool:
    """Return whether *model_dir* contains a converted OpenVINO IR model."""

    model_dir = Path(model_dir)
    return model_dir.is_dir() and any((model_dir / marker).exists() for marker in _IR_MARKERS)


def is_downloaded(cfg: ModelConfig, base_dir: Path) -> bool:
    return is_openvino_model_dir(cfg.abs_path(base_dir))


def status_label(status: str) -> str:
    return _STATUS_LABELS.get(status, "Unknown")


def _default_progress(status: str, label: str) -> dict:
    phase = {
        "queued_convert": "queued",
        "ready_to_load": "ready",
        "not_downloaded": "idle",
        "loaded": "ready",
    }.get(status, status)
    percent = 100.0 if status in {"loaded", "ready_to_load"} else None
    return {
        "phase": phase,
        "message": label,
        "percent": percent,
        "started_at": None,
        "updated_at": None,
        "log_tail": [],
    }


def _normalize_progress(progress: dict | None, status: str, label: str) -> dict:
    if not isinstance(progress, dict):
        return _default_progress(status, label)
    return {
        "phase": progress.get("phase") or _default_progress(status, label)["phase"],
        "message": progress.get("message") or label,
        "percent": progress.get("percent"),
        "started_at": progress.get("started_at"),
        "updated_at": progress.get("updated_at"),
        "log_tail": list(progress.get("log_tail") or [])[-10:],
    }


def _progress_badge(progress: dict) -> str:
    phase = str(progress.get("phase") or "").lower()
    label = _PROGRESS_PHASE_LABELS.get(phase, phase.replace("_", " ").title() or "Working")
    icon = _PROGRESS_PHASE_ICONS.get(phase, "•")
    percent = progress.get("percent")
    if percent is None:
        return f"{icon} {label}"
    try:
        return f"{icon} {label} {float(percent):.0f}%"
    except (TypeError, ValueError):
        return f"{icon} {label}"


def make_catalog_entry(
    cfg: ModelConfig,
    *,
    loaded: bool,
    queued: bool,
    loading: bool,
    downloaded: bool,
    converting: bool = False,
    cancelled: bool = False,
    device: str | None = None,
    busy: bool = False,
    error: str | None = None,
    progress: dict | None = None,
) -> dict:
    """Build one UI/API-facing status entry."""

    if loaded:
        status = "loaded"
    elif error:
        status = "error"
    elif cancelled:
        status = "cancelled"
    elif converting:
        status = "converting"
    elif queued:
        status = "queued"
    elif loading:
        status = "loading"
    elif downloaded:
        status = "ready_to_load"
    else:
        status = "not_downloaded"

    is_busy_state = status in {"queued", "loading", "queued_convert", "converting"}
    label = "Conversion failed" if status == "error" and not downloaded else status_label(status)
    progress_payload = _normalize_progress(progress, status, label)

    display_name = cfg.name
    if progress_payload.get("message") and (is_busy_state or status == "error"):
        badge = _progress_badge(progress_payload)
        label = progress_payload["message"]
        if progress_payload.get("percent") is not None and is_busy_state:
            try:
                label = f"{label} ({float(progress_payload['percent']):.0f}%)"
            except (TypeError, ValueError):
                pass
        display_name = f"{cfg.name} — {badge}"

    capabilities = list(cfg.capabilities)
    return {
        "id": cfg.id,
        "name": display_name,
        "description": cfg.description,
        "status": status,
        "status_label": label,
        "is_loaded": loaded,
        "is_loading": is_busy_state,
        "is_downloaded": downloaded,
        "device": device,
        "recommended_device": cfg.recommended_device,
        "weight_format": cfg.weight_format,
        "source_model": cfg.source_model,
        "max_context_len": cfg.max_context_len,
        "max_output_tokens": cfg.max_output_tokens,
        "trust_remote_code": cfg.trust_remote_code,
        "backend": cfg.backend,
        "capabilities": capabilities,
        "supports_vision": cfg.supports_vision,
        "input_modalities": ["text", "image"] if cfg.supports_vision else ["text"],
        "can_load": (not loaded) and downloaded and not is_busy_state,
        "can_convert": (not loaded)
        and (not downloaded)
        and bool(cfg.source_model)
        and not is_busy_state,
        "can_unload": loaded and not busy,
        "can_delete": (not loaded) and downloaded and not is_busy_state,
        "error": error,
        "progress": progress_payload,
    }


def save_catalog(models_file: Path, catalog: dict[str, ModelConfig]) -> None:
    """Save the catalog atomically."""

    data = {}
    for model_id, cfg in catalog.items():
        data[model_id] = {
            "name": cfg.name,
            "description": cfg.description,
            "backend": cfg.backend,
            "model_path": cfg.model_path,
            "source_model": cfg.source_model,
            "weight_format": cfg.weight_format,
            "recommended_device": cfg.recommended_device,
            "max_context_len": cfg.max_context_len,
            "max_output_tokens": cfg.max_output_tokens,
            "trust_remote_code": cfg.trust_remote_code,
        }
    models_file = Path(models_file)
    models_file.parent.mkdir(parents=True, exist_ok=True)
    temp = models_file.with_suffix(models_file.suffix + ".tmp")
    temp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    temp.replace(models_file)
