"""Strict model-ID resolution for OpenAI-compatible inference requests."""

from __future__ import annotations

import functools
from typing import Any

from app.hardware_advisor import parse_auto_model

_INSTALL_FLAG = "_STRICT_MODEL_RESOLUTION_INSTALLED"


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
        except ValueError:
            # Preserve the advisor's existing actionable UnknownModel error for malformed
            # auto profiles rather than replacing it with a generic unknown-ID message.
            return original_resolve_engine(self, model_id)

        if auto_profile is not None:
            return original_resolve_engine(self, model_id)
        if text not in self.catalog:
            label = text or str(model_id or "")
            raise manager_module.UnknownModel(f"Unknown model '{label}'")
        return original_resolve_engine(self, text)

    manager_class.resolve_engine = resolve_engine_exact
    setattr(manager_class, _INSTALL_FLAG, True)


__all__ = ["install_model_resolution_safety"]
