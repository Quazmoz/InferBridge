"""Policy-aware model-library recommendations without mutating precision identity."""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from app import model_registry as registry
from app.model_library_service import ModelLibraryService as _BaseModelLibraryService
from app.quantization_policy import recommend_quantization

_PROFILE: ContextVar[str] = ContextVar("model_library_profile", default="balanced")


class ModelLibraryService(_BaseModelLibraryService):
    """Apply the central quantization policy to model-library recommendations.

    The base service predates precision-specific catalog siblings and can recommend
    converting an FP16-named definition to a different weight format in place. That
    makes the catalog identity, storage path, recovery metadata, and benchmark evidence
    disagree about the artifact on disk. Keep every card's conversion action pinned to
    its declared precision and point users at a separate registered sibling when the
    active hardware/profile policy prefers another format.
    """

    def snapshot(
        self,
        *,
        profile: str = "balanced",
        query: str = "",
        include_all: bool = False,
    ) -> dict[str, Any]:
        token = _PROFILE.set(profile)
        try:
            return super().snapshot(profile=profile, query=query, include_all=include_all)
        finally:
            _PROFILE.reset(token)

    def _precision_variant(
        self,
        cfg: registry.ModelConfig,
        weight_format: str,
    ) -> registry.ModelConfig | None:
        candidates = [
            candidate
            for candidate in self.manager.catalog.values()
            if candidate.backend == cfg.backend
            and candidate.source_model == cfg.source_model
            and candidate.weight_format == weight_format
        ]
        if not candidates:
            return None
        candidates.sort(
            key=lambda candidate: (
                candidate.max_context_len != cfg.max_context_len,
                candidate.max_output_tokens != cfg.max_output_tokens,
                candidate.id,
            )
        )
        return candidates[0]

    def _entry(self, model_id: str, manifest_entry: dict[str, Any] | None) -> dict[str, Any] | None:
        item = super()._entry(model_id, manifest_entry)
        if item is None:
            return None

        cfg = self.manager.catalog.get(model_id)
        if cfg is None:
            return item
        profile = _PROFILE.get()
        advisor = getattr(self.manager, "advisor", None)
        snapshot = advisor.hardware_snapshot() if advisor is not None else None
        current_device = (
            advisor.recommend_device(cfg, profile=profile, snapshot=snapshot)
            if advisor is not None
            else str(cfg.recommended_device or "CPU")
        )

        policy = recommend_quantization(
            backend=cfg.backend,
            device=current_device,
            profile=profile,
        )
        preferred = self._precision_variant(cfg, policy.weight_format)
        preferred_device = current_device
        if preferred is not None and advisor is not None:
            preferred_device = advisor.recommend_device(
                preferred,
                profile=profile,
                snapshot=snapshot,
            )

        if preferred is None and policy.weight_format != cfg.weight_format:
            reason = (
                f"{policy.reason} No separate {policy.weight_format.upper()} variant is registered, "
                f"so this {cfg.weight_format.upper()} definition stays unchanged."
            )
        elif preferred is not None and preferred.id != cfg.id:
            reason = (
                f"{policy.reason} Prefer {preferred.name} for that precision; this card remains "
                f"{cfg.weight_format.upper()} so conversion never changes its model identity in place."
            )
        else:
            reason = policy.reason

        item["recommended_quantization"] = {
            # The existing UI uses these two fields for the current card's conversion
            # action, so they must describe the current catalog identity exactly.
            "format": cfg.weight_format,
            "device": current_device,
            "reason": reason,
            # These fields describe the policy-preferred sibling without asking the UI
            # to overwrite the current ID/path with another precision.
            "preferred_format": policy.weight_format,
            "preferred_model_id": preferred.id if preferred is not None else None,
            "preferred_model_name": preferred.name if preferred is not None else None,
            "preferred_device": preferred_device,
            "group_size": policy.group_size,
            "ratio": policy.ratio,
            "sym": policy.sym,
        }
        return item
