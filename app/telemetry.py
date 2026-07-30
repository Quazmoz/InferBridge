"""System telemetry for the status panel: memory, CPU, model disk footprint."""

from __future__ import annotations

import logging
import os
import shutil
import threading
import time
from collections import OrderedDict
from pathlib import Path

try:
    import psutil
except ImportError:  # pragma: no cover - psutil is a hard dependency at runtime
    psutil = None  # type: ignore[assignment]

logger = logging.getLogger("ov-llm.telemetry")

# The browser polls system status frequently. Converted model directories can contain
# many files, so repeatedly walking the same tree wastes I/O and can contend with model
# conversion. Keep a very short, bounded cache while preserving an explicit bypass and
# invalidation hook for callers that require an immediate refresh.
_DIR_SIZE_CACHE_TTL_SECONDS = 2.0
_DIR_SIZE_CACHE_MAX_ENTRIES = 16
_dir_size_cache: OrderedDict[str, tuple[float, int]] = OrderedDict()
_dir_size_cache_lock = threading.Lock()


def dir_size_bytes(path: str | Path) -> int:
    """Total size of a directory tree in bytes (0 if missing / unreadable)."""
    total = 0
    try:
        for dirpath, _dirs, files in os.walk(path):
            for name in files:
                try:
                    total += (Path(dirpath) / name).stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def _dir_size_cache_key(path: str | Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def clear_dir_size_cache(path: str | Path | None = None) -> None:
    """Clear one cached directory-size result or the complete bounded cache."""

    with _dir_size_cache_lock:
        if path is None:
            _dir_size_cache.clear()
        else:
            _dir_size_cache.pop(_dir_size_cache_key(path), None)


def cached_dir_size_bytes(
    path: str | Path,
    *,
    cache_seconds: float = _DIR_SIZE_CACHE_TTL_SECONDS,
) -> int:
    """Return a briefly cached directory size to avoid duplicate polling scans.

    A non-positive ``cache_seconds`` value bypasses the cache. The directory walk is
    serialized so simultaneous status requests cannot launch duplicate scans of a
    multi-gigabyte model tree.
    """

    ttl = max(float(cache_seconds), 0.0)
    if ttl <= 0:
        return dir_size_bytes(path)

    key = _dir_size_cache_key(path)
    with _dir_size_cache_lock:
        now = time.monotonic()
        cached = _dir_size_cache.get(key)
        if cached is not None and now - cached[0] < ttl:
            _dir_size_cache.move_to_end(key)
            return cached[1]

        size = dir_size_bytes(path)
        _dir_size_cache[key] = (time.monotonic(), size)
        _dir_size_cache.move_to_end(key)
        while len(_dir_size_cache) > _DIR_SIZE_CACHE_MAX_ENTRIES:
            _dir_size_cache.popitem(last=False)
        return size


def dir_size_gb(path: str | Path, *, cache_seconds: float = 0.0) -> float:
    size = cached_dir_size_bytes(path, cache_seconds=cache_seconds)
    return round(size / (1024**3), 2)


def _first_existing(path: Path) -> Path | None:
    """Walk up from ``path`` to the first directory that exists (for disk_usage)."""
    for candidate in (path, *path.parents):
        if candidate.exists():
            return candidate
    return None


def disk_stats(
    models_dir: str | Path,
    *,
    cache_seconds: float = _DIR_SIZE_CACHE_TTL_SECONDS,
) -> dict:
    """Converted-model footprint plus the real free/total space on its volume."""
    models_dir = Path(models_dir)
    stats = {
        "models_gb": dir_size_gb(models_dir, cache_seconds=cache_seconds),
        "total_gb": 0.0,
        "free_gb": 0.0,
    }
    target = _first_existing(models_dir)
    if target is not None:
        try:
            usage = shutil.disk_usage(target)
            stats["total_gb"] = round(usage.total / (1024**3), 2)
            stats["free_gb"] = round(usage.free / (1024**3), 2)
        except OSError:
            pass
    return stats


def memory_stats() -> dict:
    if psutil is None:
        return {"total_gb": 0.0, "available_gb": 0.0, "used_percent": 0.0}
    vm = psutil.virtual_memory()
    return {
        "total_gb": round(vm.total / (1024**3), 2),
        "available_gb": round(vm.available / (1024**3), 2),
        "used_percent": vm.percent,
    }


def cpu_stats() -> dict:
    if psutil is None:
        return {"percent": 0.0}
    return {"percent": psutil.cpu_percent(interval=None)}


def gpu_stats() -> dict | None:
    """Return GPU memory usage statistics if an Intel/AMD GPU is available via OpenVINO.

    Returns a dict with total, free, and used memory in GB, or None if unavailable/fails.
    """
    import importlib.util

    if importlib.util.find_spec("openvino") is None:
        return None

    try:
        from runtime.device_check import available_devices, get_core

        core = get_core()
        devices = available_devices()
        gpu_device = next((d for d in devices if d.startswith("GPU")), None)
        if not gpu_device:
            return None

        try:
            total_bytes = core.get_property(gpu_device, "GPU_DEVICE_TOTAL_MEM_SIZE")
        except Exception:
            total_bytes = None

        try:
            stats = core.get_property(gpu_device, "GPU_MEMORY_STATISTICS")
        except Exception:
            stats = {}

        result = {
            "device": gpu_device,
            "full_name": str(core.get_property(gpu_device, "FULL_DEVICE_NAME")),
        }

        if total_bytes is not None:
            result["total_gb"] = round(total_bytes / (1024**3), 2)

        formatted_stats = {}
        for k, v in stats.items():
            if isinstance(v, int):
                formatted_stats[k] = v
                if any(
                    x in k.lower()
                    for x in ("size", "bytes", "free", "used", "total", "allocated", "limit")
                ):
                    formatted_stats[f"{k}_gb"] = round(v / (1024**3), 2)
            else:
                formatted_stats[k] = v

        if formatted_stats:
            result["statistics"] = formatted_stats

        used_gb = _first_stat_gb(formatted_stats, ("used", "allocated"))
        if used_gb is not None:
            result["used_gb"] = used_gb
        free_gb = _first_stat_gb(formatted_stats, ("free", "available"))
        if free_gb is not None:
            result["free_gb"] = free_gb

        return result
    except Exception as exc:
        logger.debug("Failed to query GPU telemetry: %s", exc)
        return None


def _first_stat_gb(stats: dict, keys: tuple[str, ...]) -> float | None:
    """Return the first matching statistic (by substring key preference) in GB."""
    for key in keys:
        for k, v in stats.items():
            if key in k.lower() and isinstance(v, int):
                return round(v / (1024**3), 2)
    return None
