"""Profile recommendations and loaded-model selection."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app import model_registry as registry
from app.config import BASE_DIR

from .common import PROFILE_LABELS, PROFILE_ORDER, normalize_profile, utc_now


class ProfileRankingMixin:
    def recommend_profile(
        self,
        profile: str,
        *,
        loaded_models: Mapping[str, Any] | None = None,
        loaded_devices: Mapping[str, str] | None = None,
        snapshot: Mapping[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        profile = normalize_profile(profile)
        snapshot = snapshot or self.hardware_snapshot()
        loaded_devices = loaded_devices or {}
        candidates = []
        for model_id, cfg in self.catalog.items():
            backend = str(getattr(cfg, "backend", "")).lower()
            if "embedding" in backend:
                continue
            if loaded_models is not None and model_id not in loaded_models:
                continue
            device = loaded_devices.get(model_id)
            downloaded = False
            try:
                downloaded = registry.is_downloaded(cfg, BASE_DIR)
            except Exception:
                pass
            evaluation = self.evaluate_model(
                cfg,
                downloaded=downloaded,
                loaded=loaded_models is not None and model_id in loaded_models,
                loaded_device=device,
                profile=profile,
                snapshot=snapshot,
            )
            if evaluation.get("compatibility") == "blocked":
                continue
            score = self._profile_score(cfg, evaluation, profile, loaded_device=device)
            candidates.append((score, cfg, evaluation))

        if not candidates:
            return None
        score, cfg, evaluation = max(candidates, key=lambda item: item[0])
        return {
            "profile": profile,
            "label": PROFILE_LABELS[profile],
            "model_id": cfg.id,
            "model_name": cfg.name,
            "device": evaluation["recommended_device"],
            "precision": evaluation["precision"],
            "context_length": evaluation["recommended_context_len"],
            "output_tokens": evaluation["recommended_output_tokens"],
            "compatibility": evaluation["compatibility"],
            "fit_score": evaluation["fit_score"],
            "score": round(score, 2),
            "reason": self._profile_reason(profile, cfg, evaluation),
            "warnings": evaluation["warnings"],
            "benchmark": evaluation.get("benchmark"),
        }

    def _profile_reason(
        self,
        profile: str,
        cfg: Any,
        evaluation: Mapping[str, Any],
    ) -> str:
        model = cfg.name
        device = evaluation.get("recommended_device")
        benchmark = (
            evaluation.get("benchmark")
            if isinstance(evaluation.get("benchmark"), Mapping)
            else None
        )
        if benchmark:
            speed = benchmark.get("decode_tokens_sec")
            if speed is None:
                speed = benchmark.get("tokens_sec")
            speed_text = f"{float(speed):.1f} tok/s" if speed is not None else "measured timing"
            return (
                f"{model} on {device} is supported by current local benchmark evidence "
                f"({speed_text}) plus the selected {PROFILE_LABELS[profile].lower()} fit policy."
            )
        if profile == "fastest":
            return (
                f"{model} on {device} has the strongest estimated responsiveness "
                "among compatible choices."
            )
        if profile == "best-quality":
            return (
                f"{model} is the highest-capability model estimated to fit this PC "
                "without a blocking preflight result."
            )
        if profile == "lowest-memory":
            return (
                f"{model} has the smallest compatible runtime-memory estimate "
                f"({evaluation.get('runtime_memory_gb')} GB)."
            )
        if profile == "lowest-power":
            return (
                f"{model} on {device} prioritizes efficient Intel acceleration "
                "and a compact runtime footprint."
            )
        return (
            f"{model} on {device} offers the best balance of estimated quality, speed, "
            "memory fit, and compatible benchmark evidence."
        )

    def select_loaded_model(
        self,
        profile: str,
        engines: Mapping[str, Any],
        devices: Mapping[str, str],
    ) -> str | None:
        recommendation = self.recommend_profile(
            profile,
            loaded_models=engines,
            loaded_devices=devices,
        )
        return recommendation.get("model_id") if recommendation else None

    def summary(
        self,
        engines: Mapping[str, Any],
        devices: Mapping[str, str],
    ) -> dict[str, Any]:
        snapshot = self.hardware_snapshot()
        profiles = {
            profile: self.recommend_profile(profile, snapshot=snapshot)
            for profile in PROFILE_ORDER
        }
        loaded_profiles = {
            profile: self.recommend_profile(
                profile,
                loaded_models=engines,
                loaded_devices=devices,
                snapshot=snapshot,
            )
            for profile in PROFILE_ORDER
        }
        models = []
        for model_id, cfg in self.catalog.items():
            try:
                downloaded = registry.is_downloaded(cfg, BASE_DIR)
            except Exception:
                downloaded = False
            models.append(
                {
                    "id": model_id,
                    "name": cfg.name,
                    "backend": cfg.backend,
                    **self.evaluate_model(
                        cfg,
                        downloaded=downloaded,
                        loaded=model_id in engines,
                        loaded_device=devices.get(model_id),
                        snapshot=snapshot,
                    ),
                }
            )
        return {
            "schema_version": 1,
            "generated_at": utc_now(),
            "default_profile": "balanced",
            "profile_order": list(PROFILE_ORDER),
            "profile_labels": dict(PROFILE_LABELS),
            "auto_model_examples": [
                "auto",
                *[f"auto:{item}" for item in PROFILE_ORDER],
            ],
            "hardware": snapshot,
            "profiles": profiles,
            "loaded_profiles": loaded_profiles,
            "models": models,
            "estimates_notice": (
                "Size, memory, power, and compilation values are conservative estimates "
                "until a successful benchmark on this hardware is available."
            ),
        }
