"""Compatibility fixes for OpenVINO GenAI NPU pipeline construction.

The application supports a broad range of OpenVINO GenAI releases. NPU-backed
LLM pipelines need both the prompt and response token budgets at compile time,
while VLM pipelines require NPU properties under ``DEVICE_PROPERTIES``. This
module installs a narrow compatibility shim before :mod:`app.model_manager`
binds the engine factory.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from runtime import openvino_engine as engine

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DIRECT_NPU_RE = re.compile(r"^NPU(?:\.\d+)?$")
_ORIGINAL_CREATE_ENGINE = engine.create_engine
_ORIGINAL_TEXT_ENGINE = engine.OpenVINOEngine
_ORIGINAL_EMBEDDING_ENGINE = engine.OpenVINOEmbeddingEngine
_ORIGINAL_VISION_ENGINE = engine.OpenVINOVisionEngine


def is_direct_npu(device: str | None) -> bool:
    """Return whether *device* targets one physical NPU rather than AUTO/MULTI."""

    try:
        normalized = engine.normalize_device(device)
    except Exception:
        return False
    return bool(_DIRECT_NPU_RE.fullmatch(normalized))


def _positive_int(value: object) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _catalog_candidates() -> list[Path]:
    candidates: list[Path] = []
    configured = os.environ.get("OV_LLM_MODELS_FILE", "").strip()
    if configured:
        path = Path(configured).expanduser()
        candidates.append(path if path.is_absolute() else _PROJECT_ROOT / path)
    candidates.extend((_PROJECT_ROOT / "models.json", Path.cwd() / "models.json"))

    unique: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve(strict=False)
        if resolved not in seen:
            seen.add(resolved)
            unique.append(resolved)
    return unique


def _catalog_response_budget(model_id: str, model_path: str) -> int | None:
    resolved_model_path = Path(model_path).expanduser().resolve(strict=False)
    for catalog_path in _catalog_candidates():
        try:
            with catalog_path.open(encoding="utf-8-sig") as file:
                catalog = json.load(file)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(catalog, dict):
            continue

        raw = catalog.get(model_id)
        if isinstance(raw, dict):
            budget = _positive_int(raw.get("max_output_tokens"))
            if budget is not None:
                return budget

        # Also match by model path so aliases and externally registered IDs keep
        # the exact response budget stored in the active catalog.
        for entry in catalog.values():
            if not isinstance(entry, dict):
                continue
            raw_path = entry.get("model_path")
            if not isinstance(raw_path, str) or not raw_path.strip():
                continue
            candidate = Path(raw_path).expanduser()
            if not candidate.is_absolute():
                candidate = catalog_path.parent / candidate
            if candidate.resolve(strict=False) != resolved_model_path:
                continue
            budget = _positive_int(entry.get("max_output_tokens"))
            if budget is not None:
                return budget
    return None


def _fallback_response_budget(max_prompt_len: int | None) -> int:
    """Choose a bounded fallback when a nonstandard catalog cannot be resolved."""

    prompt_budget = _positive_int(max_prompt_len) or 384
    # The built-in catalogs reserve one output token for roughly every three
    # prompt tokens. Keep the fallback conservative and within common NPU limits.
    return max(128, min(1024, prompt_budget // 3))


def resolve_response_budget(
    model_id: str,
    model_path: str,
    max_prompt_len: int | None,
    explicit: int | None = None,
) -> int:
    """Resolve the NPU response budget from an explicit value or model catalog."""

    configured = _positive_int(explicit)
    if configured is not None:
        return configured
    catalog_budget = _catalog_response_budget(model_id, model_path)
    if catalog_budget is not None:
        return catalog_budget
    fallback = _fallback_response_budget(max_prompt_len)
    engine.logger.warning(
        "Could not resolve max_output_tokens for NPU model '%s'; using inferred "
        "MIN_RESPONSE_LEN=%d.",
        model_id,
        fallback,
    )
    return fallback


def build_plugin_config(
    device: str,
    max_prompt_len: int | None,
    cache_dir: str | Path | None = None,
    *,
    max_response_len: int | None = None,
    backend: str = "openvino-genai",
    kv_cache_precision: str | None = None,
) -> dict[str, Any]:
    """Build a complete, backend-aware OpenVINO plugin configuration.

    Direct NPU LLM/VLM compilation receives both halves of the context window.
    Embedding pipelines never receive generation-only NPU properties.
    """

    normalized = engine.normalize_device(device)
    backend_lower = str(backend or "").lower()
    is_embedding = "embedding" in backend_lower
    config: dict[str, Any] = {}

    if is_direct_npu(normalized) and not is_embedding and max_prompt_len:
        config["MAX_PROMPT_LEN"] = int(max_prompt_len)
        config["MIN_RESPONSE_LEN"] = _positive_int(max_response_len) or 128
    if not is_embedding:
        # CPU/GPU only; the NPU compiles its own static KV layout.
        config.update(engine.kv_cache_precision_property(normalized, kv_cache_precision))

    if cache_dir:
        try:
            cache_path = Path(cache_dir)
            cache_path.mkdir(parents=True, exist_ok=True)
            config["CACHE_DIR"] = str(cache_path)
        except Exception as exc:
            engine.logger.warning("Failed to create cache directory '%s': %s", cache_dir, exc)

    return config


def _vlm_config(plugin_config: dict[str, Any]) -> dict[str, Any]:
    """Nest every NPU VLM property under ``DEVICE_PROPERTIES`` as required."""

    return {"DEVICE_PROPERTIES": {"NPU": dict(plugin_config)}}


class OpenVINOVisionEngine(_ORIGINAL_VISION_ENGINE):
    """VLM wrapper with correct direct-NPU and indexed-NPU configuration."""

    def __init__(
        self,
        model_id: str,
        model_path: str,
        device: str,
        plugin_config: dict | None = None,
        draft_model_path: str | None = None,
    ) -> None:
        if draft_model_path:
            raise RuntimeError("Speculative draft models are not supported by the VLM backend.")

        import openvino_genai as ov_genai

        if not hasattr(ov_genai, "VLMPipeline"):
            raise RuntimeError(
                "This OpenVINO GenAI version does not provide VLMPipeline. Upgrade openvino-genai."
            )

        self._ov = ov_genai
        self.model_id = model_id
        self.model_path = str(model_path)
        self.device = engine.normalize_device(device)
        self._closed = False
        config = dict(plugin_config or {})

        engine.logger.info(
            "Loading vision model '%s' on %s from %s", model_id, self.device, self.model_path
        )
        if is_direct_npu(self.device) and config:
            self._pipe = ov_genai.VLMPipeline(
                self.model_path,
                self.device,
                config=_vlm_config(config),
            )
        elif config:
            self._pipe = ov_genai.VLMPipeline(self.model_path, self.device, **config)
        else:
            self._pipe = ov_genai.VLMPipeline(self.model_path, self.device)
        self._tokenizer = self._pipe.get_tokenizer()
        engine.logger.info("Vision model '%s' ready on %s", model_id, self.device)


def create_engine(
    *,
    model_id: str,
    model_path: str,
    device: str,
    max_prompt_len: int | None = None,
    force_mock: bool = False,
    cache_dir: str | Path | None = None,
    backend: str = "openvino-genai",
    draft_model_path: str | None = None,
    max_response_len: int | None = None,
    kv_cache_precision: str | None = None,
) -> engine.BaseEngine:
    """Create an engine, adding complete context configuration for direct NPUs."""

    normalized = engine.normalize_device(device)
    if force_mock or not engine.is_openvino_available() or not is_direct_npu(normalized):
        return _ORIGINAL_CREATE_ENGINE(
            model_id=model_id,
            model_path=model_path,
            device=normalized,
            max_prompt_len=max_prompt_len,
            force_mock=force_mock,
            cache_dir=cache_dir,
            backend=backend,
            draft_model_path=draft_model_path,
            kv_cache_precision=kv_cache_precision,
        )

    backend_lower = backend.lower()
    is_embedding = "embedding" in backend_lower
    is_vision = engine.multimodal.backend_supports_vision(backend_lower)
    response_budget = None
    if not is_embedding:
        response_budget = resolve_response_budget(
            model_id,
            model_path,
            max_prompt_len,
            explicit=max_response_len,
        )
    plugin_config = build_plugin_config(
        normalized,
        max_prompt_len,
        cache_dir,
        max_response_len=response_budget,
        backend=backend,
    )

    if is_embedding:
        return _ORIGINAL_EMBEDDING_ENGINE(model_id, model_path, normalized, plugin_config)
    if is_vision:
        return OpenVINOVisionEngine(
            model_id,
            model_path,
            normalized,
            plugin_config,
            draft_model_path=draft_model_path,
        )
    return _ORIGINAL_TEXT_ENGINE(
        model_id,
        model_path,
        normalized,
        plugin_config,
        draft_model_path=draft_model_path,
    )


def install_openvino_genai_compat() -> None:
    """Install the compatibility functions once on ``runtime.openvino_engine``."""

    if getattr(engine, "_NPU_CONTEXT_COMPAT_INSTALLED", False):
        return
    engine.build_plugin_config = build_plugin_config
    engine.OpenVINOVisionEngine = OpenVINOVisionEngine
    engine.create_engine = create_engine
    engine._NPU_CONTEXT_COMPAT_INSTALLED = True
