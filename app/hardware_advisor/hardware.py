"""Stable hardware and driver preflight collection."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
from collections.abc import Mapping
from functools import lru_cache
from typing import Any

from runtime import device_check

from .common import base_device, safe_float


def package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def cpu_details() -> dict[str, Any]:
    physical = logical = 0
    frequency_mhz = None
    try:
        import psutil

        physical = int(psutil.cpu_count(logical=False) or 0)
        logical = int(psutil.cpu_count(logical=True) or 0)
        freq = psutil.cpu_freq()
        if freq and freq.max:
            frequency_mhz = round(float(freq.max), 1)
    except Exception:
        logical = int(os.cpu_count() or 0)
    name = platform.processor() or os.environ.get("PROCESSOR_IDENTIFIER") or platform.machine()
    return {
        "name": str(name or "Unknown CPU").strip(),
        "architecture": platform.machine() or "unknown",
        "physical_cores": physical,
        "logical_cores": logical,
        "max_frequency_mhz": frequency_mhz,
    }


def json_safe(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Mapping):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [json_safe(item) for item in value]
    return str(value)


@lru_cache(maxsize=1)
def device_details() -> list[dict[str, Any]]:
    available = device_check.available_devices()
    if not available:
        return []
    try:
        core = device_check.get_core()
    except Exception:
        return [{"device": device, "base": base_device(device)} for device in available]
    details = []
    properties = (
        ("FULL_DEVICE_NAME", "full_name"),
        ("DRIVER_VERSION", "driver_version"),
        ("DEVICE_ARCHITECTURE", "architecture"),
        ("DEVICE_TYPE", "type"),
        ("OPTIMIZATION_CAPABILITIES", "optimization_capabilities"),
        ("GPU_DEVICE_TOTAL_MEM_SIZE", "total_memory_bytes"),
        # Intel NPU plugin: ov::intel_npu::driver_version (integer, e.g. 1688).
        ("NPU_DRIVER_VERSION", "npu_driver_version"),
    )
    for device in available:
        item = {"device": device, "base": base_device(device)}
        for prop, output_key in properties:
            try:
                value = core.get_property(device, prop)
                item[output_key] = json_safe(value)
                if output_key == "total_memory_bytes":
                    item["total_memory_gb"] = round(safe_float(value) / (1024**3), 2)
            except Exception:
                continue
        if item.get("npu_driver_version") is not None and not item.get("driver_version"):
            item["driver_version"] = str(item["npu_driver_version"])
        details.append(item)
    return details


def fingerprint(snapshot: Mapping[str, Any]) -> str:
    stable = {
        "os": snapshot.get("os"),
        "cpu": snapshot.get("cpu"),
        "memory_total_gb": snapshot.get("memory", {}).get("total_gb"),
        "devices": [
            {
                key: item.get(key)
                for key in ("device", "full_name", "driver_version", "architecture")
            }
            for item in snapshot.get("devices", [])
        ],
        "openvino": snapshot.get("runtime", {}).get("openvino"),
        "openvino_genai": snapshot.get("runtime", {}).get("openvino_genai"),
    }
    payload = json.dumps(stable, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]
