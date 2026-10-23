"""Conservative model footprint and first-load estimates."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.config import BASE_DIR
from app.context_configuration import kv_cache_gib, model_metadata

from .common import base_device, infer_parameter_count_b, safe_float


class ModelEstimateMixin:
    def estimate_model(self, cfg: Any, *, device: str | None = None) -> dict[str, Any]:
        parameter_count_b = infer_parameter_count_b(cfg.id, cfg.name, cfg.source_model)
        precision = str(getattr(cfg, "weight_format", "fp16") or "fp16").lower()
        bytes_per_parameter = {"fp16": 2.0, "int8": 1.05, "int4": 0.58}.get(precision, 2.0)

        source_download_gb = max(parameter_count_b * 2.10, 0.06)
        estimated_converted_gb = max(parameter_count_b * bytes_per_parameter * 1.08, 0.04)
        actual_converted_gb = self._actual_converted_size_gb(cfg)
        converted_gb = actual_converted_gb or estimated_converted_gb

        context_len = max(int(getattr(cfg, "max_context_len", 2048) or 2048), 128)
        kv_cache_gb = kv_cache_gib(model_metadata(cfg, BASE_DIR), context_len)
        # This is a system-RAM *lower bound* if architecture is incomplete.
        # Even with metadata, the FP16 KV assumption need not match device allocation.
        runtime_memory_gb = max(converted_gb * 1.22 + (kv_cache_gb or 0) + 0.35, 0.5)

        target = device or getattr(cfg, "recommended_device", "CPU") or "CPU"
        base = base_device(target)
        multiplier = {"CPU": 1.0, "GPU": 2.2, "NPU": 4.0}.get(base, 1.8)
        first_load_seconds = 4.0 + parameter_count_b * 2.0 * multiplier + converted_gb * multiplier
        benchmark = self._latest_benchmark(cfg.id, target)
        evidence = "estimated"
        if benchmark and benchmark.get("load_time_ms") is not None:
            first_load_seconds = max(safe_float(benchmark.get("load_time_ms")) / 1000.0, 0.0)
            evidence = "measured"

        return {
            "parameter_count_b": round(parameter_count_b, 3),
            "precision": precision,
            "download_size_gb": round(source_download_gb, 2),
            "converted_size_gb": round(converted_gb, 2),
            "converted_size_source": "measured" if actual_converted_gb else "estimated",
            "runtime_memory_gb": round(runtime_memory_gb, 2),
            "kv_cache_gb": round(kv_cache_gb, 3) if kv_cache_gb is not None else None,
            "kv_cache_source": "architectural-fp16-estimate"
            if kv_cache_gb is not None
            else "unavailable",
            "runtime_memory_source": "estimated-lower-bound"
            if kv_cache_gb is None
            else "estimated",
            "first_load_seconds": round(first_load_seconds, 1),
            "first_load_source": evidence,
            "target_device": target,
        }

    def _candidate_devices(self, cfg: Any, snapshot: Mapping[str, Any]) -> list[str]:
        available = {base_device(item) for item in snapshot.get("available_devices", [])}
        candidates: list[str] = []
        preferred = base_device(getattr(cfg, "recommended_device", "CPU"))
        if preferred in available or preferred == "CPU":
            candidates.append(preferred)
        for candidate in ("NPU", "GPU", "CPU"):
            if candidate in available or candidate == "CPU":
                if candidate not in candidates:
                    candidates.append(candidate)
        return candidates or ["CPU"]

    def _gpu_total_gb(self, snapshot: Mapping[str, Any]) -> float | None:
        gpu = snapshot.get("gpu")
        if isinstance(gpu, dict):
            if gpu.get("total_memory_gb") is not None:
                return safe_float(gpu.get("total_memory_gb"))
            if gpu.get("total_gb") is not None:
                return safe_float(gpu.get("total_gb"))
        return None
