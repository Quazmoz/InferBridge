"""Compatibility warnings and recommended generation budgets."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.quantization_policy import recommend_quantization

from .common import base_device, normalize_profile, safe_float


class PreflightMixin:
    def evaluate_model(
        self,
        cfg: Any,
        *,
        downloaded: bool = False,
        loaded: bool = False,
        loaded_device: str | None = None,
        profile: str = "balanced",
        snapshot: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        profile = normalize_profile(profile)
        snapshot = snapshot or self.hardware_snapshot()
        target = loaded_device or self.recommend_device(cfg, profile=profile, snapshot=snapshot)
        quantization = recommend_quantization(
            backend=str(getattr(cfg, "backend", "openvino-genai")),
            device=target,
            profile=profile,
        )
        estimates = self.estimate_model(cfg, device=target)
        memory = snapshot.get("memory", {})
        disk = snapshot.get("disk", {})
        available_ram = safe_float(memory.get("available_gb"))
        total_ram = safe_float(memory.get("total_gb"))
        free_disk = safe_float(disk.get("free_gb"))
        runtime_memory = safe_float(estimates.get("runtime_memory_gb"))
        required_disk = (
            0.35
            if downloaded
            else (
                safe_float(estimates.get("download_size_gb"))
                + safe_float(estimates.get("converted_size_gb")) * 1.15
                + 1.0
            )
        )
        base = base_device(target)
        available_bases = {base_device(item) for item in snapshot.get("available_devices", [])}

        warnings: list[dict[str, str]] = []

        def warn(code: str, severity: str, message: str) -> None:
            warnings.append({"code": code, "severity": severity, "message": message})

        if base != "CPU" and base not in available_bases:
            warn("device-missing", "blocking", f"{base} is not reported by OpenVINO on this PC.")
        if not loaded and free_disk and free_disk < required_disk:
            warn(
                "disk-insufficient",
                "blocking",
                f"About {required_disk:.1f} GB is required, but only {free_disk:.1f} GB is free.",
            )
        elif not loaded and free_disk and free_disk < required_disk * 1.35:
            warn(
                "disk-tight",
                "warning",
                f"Free disk space is close to the estimated {required_disk:.1f} GB preparation requirement.",
            )
        if not loaded and available_ram and available_ram < runtime_memory * 0.85:
            warn(
                "ram-insufficient",
                "blocking",
                f"Estimated runtime memory is {runtime_memory:.1f} GB with {available_ram:.1f} GB currently available.",
            )
        elif not loaded and available_ram and available_ram < runtime_memory * 1.25:
            warn(
                "ram-tight",
                "warning",
                f"Estimated runtime memory is {runtime_memory:.1f} GB; close other applications before loading.",
            )
        if base == "GPU":
            gpu_total = self._gpu_total_gb(snapshot)
            if gpu_total and gpu_total < runtime_memory * 0.75:
                warn(
                    "gpu-memory-tight",
                    "warning",
                    f"Reported GPU memory is {gpu_total:.1f} GB versus a {runtime_memory:.1f} GB runtime estimate; shared memory may be required.",
                )
        if base == "NPU" and estimates["parameter_count_b"] > 4.5:
            warn(
                "npu-large-model",
                "warning",
                "This is a large model for an Intel NPU. Compilation and runtime support vary by platform and driver; CPU or GPU is safer until benchmarked.",
            )
        if base == "NPU" and int(getattr(cfg, "max_context_len", 2048)) > 4096:
            warn(
                "npu-context",
                "warning",
                "Long contexts can sharply increase NPU compilation and memory cost; the advisor will recommend a shorter context initially.",
            )
        if estimates["parameter_count_b"] >= 14 and total_ram and total_ram < 48:
            warn(
                "large-model-system-memory",
                "warning",
                "Models at this size normally need substantial system memory and may page heavily on this PC.",
            )
        source = str(getattr(cfg, "source_model", "")).lower()
        if any(owner in source for owner in ("meta-llama/", "google/gemma")):
            warn(
                "gated-model",
                "info",
                "This source may require accepting its license and setting HF_TOKEN before download.",
            )
        if bool(getattr(cfg, "trust_remote_code", False)):
            warn(
                "remote-code",
                "warning",
                "This catalog entry permits repository code during conversion. Review the source before continuing.",
            )
        if estimates["first_load_seconds"] >= 180:
            warn(
                "slow-first-load",
                "info",
                "The first compilation is estimated to take several minutes; later loads can reuse OpenVINO caches.",
            )

        blocking = [item for item in warnings if item["severity"] == "blocking"]
        caution = [item for item in warnings if item["severity"] == "warning"]
        status = "blocked" if blocking else "caution" if caution else "compatible"

        max_context = int(getattr(cfg, "max_context_len", 2048))
        recommended_context = max_context
        if base == "NPU":
            recommended_context = min(recommended_context, 4096)
        if available_ram and available_ram < runtime_memory * 1.5:
            recommended_context = max(1024, min(recommended_context, max_context // 2))
        if "embedding" in str(getattr(cfg, "backend", "")).lower():
            recommended_output = 0
        else:
            recommended_output = min(
                int(getattr(cfg, "max_output_tokens", 512) or 512),
                max(128, recommended_context // 4),
            )

        fit_score = 100.0
        fit_score -= len(blocking) * 55.0
        fit_score -= len(caution) * 12.0
        if available_ram and runtime_memory:
            fit_score += min((available_ram / runtime_memory) - 1.0, 1.0) * 8.0
        fit_score = round(max(0.0, min(fit_score, 100.0)), 1)

        benchmark = self._latest_benchmark(cfg.id, target)
        return {
            **estimates,
            "profile": profile,
            "compatibility": status,
            "fit_score": fit_score,
            "required_free_disk_gb": round(required_disk, 2),
            "recommended_device": target,
            "recommended_context_len": recommended_context,
            "recommended_output_tokens": recommended_output,
            "recommended_weight_format": quantization.weight_format,
            "quantization_recommendation": {
                "weight_format": quantization.weight_format,
                "reason": quantization.reason,
                "group_size": quantization.group_size,
                "ratio": quantization.ratio,
                "sym": quantization.sym,
            },
            "warnings": warnings,
            "requires_confirmation": bool(blocking or caution),
            "downloaded": bool(downloaded),
            "loaded": bool(loaded),
            "benchmark": benchmark,
        }
