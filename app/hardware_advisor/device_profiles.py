"""Device and profile scoring helpers."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from app.quantization_policy import estimated_quality_penalty, profile_precision_bonus

from .common import base_device, clamp, infer_parameter_count_b, normalize_profile, safe_float


class DeviceProfileMixin:
    def _quality_score(self, cfg: Any) -> float:
        params = infer_parameter_count_b(cfg.id, cfg.name, cfg.source_model)
        score = math.log2(max(params, 0.03) + 1.0) / math.log2(33.0) * 100.0
        text = f"{cfg.id} {cfg.name} {cfg.source_model}".lower()
        if "reason" in text or "deepseek-r1" in text:
            score += 7.0
        if "instruct" in text or "chat" in text:
            score += 3.0
        if "embedding" in str(getattr(cfg, "backend", "")).lower():
            return 0.0
        score -= estimated_quality_penalty(getattr(cfg, "weight_format", "fp16"))
        return clamp(score / 100.0) * 100.0

    @staticmethod
    def _benchmark_tokens_sec(benchmark: Mapping[str, Any] | None) -> float | None:
        if not benchmark:
            return None
        value = benchmark.get("decode_tokens_sec")
        if value is None:
            value = benchmark.get("tokens_sec")
        if value is None:
            return None
        return safe_float(value)

    def _estimated_speed_score(
        self,
        cfg: Any,
        device: str,
        benchmark: Mapping[str, Any] | None,
    ) -> float:
        measured = self._benchmark_tokens_sec(benchmark)
        if measured is not None:
            return min(measured * 4.0, 100.0)
        params = infer_parameter_count_b(cfg.id, cfg.name, cfg.source_model)
        device_factor = {"NPU": 1.35, "GPU": 1.2, "CPU": 1.0}.get(base_device(device), 0.9)
        return clamp(device_factor / math.sqrt(max(params, 0.08)) / 4.0) * 100.0

    def recommend_device(
        self,
        cfg: Any,
        *,
        profile: str = "balanced",
        snapshot: Mapping[str, Any] | None = None,
    ) -> str:
        profile = normalize_profile(profile)
        snapshot = snapshot or self.hardware_snapshot()
        candidates = self._candidate_devices(cfg, snapshot)
        params = infer_parameter_count_b(cfg.id, cfg.name, cfg.source_model)
        runtime_estimate = self.estimate_model(cfg, device="CPU")["runtime_memory_gb"]
        gpu_total = self._gpu_total_gb(snapshot)
        precision = str(getattr(cfg, "weight_format", "fp16") or "fp16").lower()

        def score(device: str) -> float:
            base = base_device(device)
            value = 0.0
            if profile == "lowest-power":
                value += {"NPU": 100, "GPU": 62, "CPU": 48}.get(base, 30)
                value -= params * 2.0
                if base == "NPU" and params > 4.5:
                    value -= 50
            elif profile == "fastest":
                value += {"NPU": 92, "GPU": 86, "CPU": 60}.get(base, 50)
                if base == "NPU" and params > 4.5:
                    value -= 45
            elif profile == "best-quality":
                value += {"GPU": 86, "CPU": 78, "NPU": 68}.get(base, 50)
            elif profile == "lowest-memory":
                value += {"NPU": 82, "GPU": 68, "CPU": 62}.get(base, 50)
            else:
                value += {"NPU": 88, "GPU": 82, "CPU": 70}.get(base, 50)
                if base == "NPU" and params > 4.5:
                    value -= 35

            if base == "NPU":
                if precision == "int4":
                    value += 10
                elif precision == "int8":
                    value -= 30

            benchmark = self._latest_benchmark(cfg.id, device)
            measured = self._benchmark_tokens_sec(benchmark)
            if measured is not None:
                value += min(measured, 40.0)
            if base == "GPU" and gpu_total and gpu_total < runtime_estimate * 0.75:
                value -= 35
            return value

        return max(candidates, key=score)

    def _profile_score(
        self,
        cfg: Any,
        evaluation: Mapping[str, Any],
        profile: str,
        *,
        loaded_device: str | None = None,
    ) -> float:
        if evaluation.get("compatibility") == "blocked":
            return -10_000.0
        device = loaded_device or str(evaluation.get("recommended_device") or "CPU")
        benchmark = (
            evaluation.get("benchmark")
            if isinstance(evaluation.get("benchmark"), dict)
            else None
        )
        speed = self._estimated_speed_score(cfg, device, benchmark)
        quality = self._quality_score(cfg)
        memory = safe_float(evaluation.get("runtime_memory_gb"), 1.0)
        fit = safe_float(evaluation.get("fit_score"), 0.0)
        params = safe_float(evaluation.get("parameter_count_b"), 1.0)
        base = base_device(device)
        precision_prior = profile_precision_bonus(
            getattr(cfg, "weight_format", "fp16"),
            profile,
            device=device,
        )

        if profile == "fastest":
            return (
                speed * 0.72
                + fit * 0.18
                - memory * 0.8
                + (8 if benchmark else 0)
                + precision_prior
            )
        if profile == "best-quality":
            return (
                quality * 0.72
                + fit * 0.20
                + speed * 0.08
                + min(params, 32) * 0.2
                + precision_prior
            )
        if profile == "lowest-memory":
            return fit * 0.35 + 70.0 / max(memory, 0.35) + speed * 0.08 + precision_prior
        if profile == "lowest-power":
            power = {"NPU": 100.0, "GPU": 68.0, "CPU": 55.0}.get(base, 45.0)
            return power * 0.58 + fit * 0.27 + speed * 0.15 - memory + precision_prior
        return (
            quality * 0.38
            + speed * 0.30
            + fit * 0.28
            - memory * 0.35
            + precision_prior
        )
