"""Strict model-ID resolution for OpenAI-compatible inference requests."""

from __future__ import annotations

import functools
from typing import Any

from app.hardware_advisor import PROFILE_ORDER, parse_auto_model

_INSTALL_FLAG = "_STRICT_MODEL_RESOLUTION_INSTALLED"
_MODEL_LABEL_LIMIT = 160


def _safe_model_label(value: Any) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    text = "".join(char for char in text if ord(char) >= 32)
    if len(text) > _MODEL_LABEL_LIMIT:
        return text[: _MODEL_LABEL_LIMIT - 1].rstrip() + "…"
    return text


def install_model_resolution_safety() -> None:
    """Reject unknown exact model IDs while preserving documented advisor auto routing.

    The retained core historically falls back from an unknown ID to the configured
    default or first loaded engine. That is unsafe for an OpenAI-compatible API because
    Chat Completions, Responses, and embeddings echo the client-supplied model ID. A
    request for model A could therefore be executed by model B while the wire response
    still claimed model A handled it.
    """

    from app import model_manager as manager_module

    manager_class = manager_module.ModelManager
    if getattr(manager_class, _INSTALL_FLAG, False):
        return
    original_resolve_engine = manager_class.resolve_engine

    @functools.wraps(original_resolve_engine)
    def resolve_engine_exact(self: Any, model_id: str):
        text = str(model_id or "").strip()
        try:
            auto_profile = parse_auto_model(text)
        except ValueError as exc:
            label = _safe_model_label(text)
            supported = ", ".join(PROFILE_ORDER)
            raise manager_module.UnknownModel(
                f"Invalid advisor model selector '{label}'. Supported profiles: {supported}."
            ) from exc

        if auto_profile is not None:
            return original_resolve_engine(self, model_id)
        if text not in self.catalog:
            label = _safe_model_label(text or model_id) or "<empty>"
            raise manager_module.UnknownModel(f"Unknown model '{label}'")
        return original_resolve_engine(self, text)

    manager_class.resolve_engine = resolve_engine_exact
    setattr(manager_class, _INSTALL_FLAG, True)


__all__ = ["install_model_resolution_safety"]
