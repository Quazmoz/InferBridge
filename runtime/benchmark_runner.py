"""Benchmark runner compatibility layer with shared locking and safe failure details."""

from __future__ import annotations

import copy
import re
from typing import Any

from app.file_locks import path_lock
from runtime import benchmark_runner_core as _core
from runtime.benchmark_runner_core import *  # noqa: F401,F403 - preserve public API

_SECRET_RE = re.compile(
    r"(hf_[A-Za-z0-9_=-]{8,}|Bearer\s+[A-Za-z0-9._~+/=-]+|token\s*[:=]\s*\S+)",
    re.IGNORECASE,
)


def _safe_error(value: Any, *, limit: int = 500) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    text = "".join(char for char in text if ord(char) >= 32)
    text = _SECRET_RE.sub("[redacted]", text)
    text = re.sub(r"[A-Za-z]:\\(?:[^\\\s]+\\)+", r"...\\", text)
    text = re.sub(r"/(?:[^/\s]+/){2,}", ".../", text)
    return text[:limit] or None


def _sanitize_run(run: dict[str, Any]) -> dict[str, Any]:
    safe = copy.deepcopy(run)
    results = safe.get("results")
    if isinstance(results, list):
        for result in results:
            if isinstance(result, dict) and result.get("error") is not None:
                result["error"] = _safe_error(result.get("error"))
    return safe


def _sanitize_runs(runs: list[Any]) -> list[Any]:
    return [_sanitize_run(run) if isinstance(run, dict) else run for run in runs]


class BenchmarkStore(_core.BenchmarkStore):
    """JSON benchmark store serialized with advisor writes and sanitized failures."""

    def __init__(self, path, *, max_runs: int = 100) -> None:
        super().__init__(path, max_runs=max_runs)
        self._lock = path_lock(self.path)
        # Upgrade existing stores in place. Old builds persisted raw native exception
        # strings, which can contain local paths or credential-like values.
        with self._lock:
            data = self._read()
            safe_runs = _sanitize_runs(data["runs"])
            if safe_runs != data["runs"]:
                self._write({"schema_version": data["schema_version"], "runs": safe_runs})

    def list_runs(self) -> list[dict[str, Any]]:
        return [run for run in _sanitize_runs(super().list_runs()) if isinstance(run, dict)]

    def append(self, run: dict[str, Any]) -> None:
        super().append(_sanitize_run(run))


# Functions defined in the retained implementation resolve this global at runtime.
_core.BenchmarkStore = BenchmarkStore


async def benchmark_model_device(*args: Any, **kwargs: Any):
    result = await _core.benchmark_model_device(*args, **kwargs)
    if result.error is not None:
        result.error = _safe_error(result.error)
    return result


async def run_benchmark_suite(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return _sanitize_run(await _core.run_benchmark_suite(*args, **kwargs))


async def certify_context_depth(*args: Any, **kwargs: Any):
    result = await _core.certify_context_depth(*args, **kwargs)
    if result.error is not None:
        result.error = _safe_error(result.error)
    return result


def __getattr__(name: str):
    return getattr(_core, name)


if __name__ == "__main__":  # pragma: no cover - exercised by the CLI
    raise SystemExit(_core.main())
