"""Persisted benchmark and converted-size evidence for the model advisor."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import BASE_DIR
from app.telemetry import dir_size_gb

from .common import base_device

_BENCHMARK_SCHEMA_VERSION = 1
_ARTIFACT_IDENTITY_FILES = (
    "openvino_model.xml",
    "openvino_model.bin",
    "openvino_language_model.xml",
    "openvino_language_model.bin",
    "config.json",
)
_ARTIFACT_MTIME_TOLERANCE_SECONDS = 2.0


def benchmark_matches_direct_device(row: Mapping[str, Any], device: str) -> bool:
    """Return whether a benchmark proved execution on one direct device."""

    expected = base_device(device)
    requested = row.get("requested_device")
    actual = row.get("actual_device")
    return (
        expected in {"CPU", "GPU", "NPU"}
        and bool(str(requested or "").strip())
        and bool(str(actual or "").strip())
        and base_device(requested) == expected
        and base_device(actual) == expected
    )


def _benchmark_evidence_rank(row: Mapping[str, Any]) -> tuple[int, int, int, int]:
    """Rank matching evidence by measurement strength, not merely append order.

    A manual Benchmark Lab run should not be displaced by a later one-shot automatic
    post-load sample on the same unchanged hardware. Within the same class, newer store
    order remains the tie-breaker in :meth:`EvidenceMixin._latest_benchmark`.
    """

    manual = 1 if row.get("automatic") is not True else 0
    try:
        methodology = max(int(row.get("methodology_version") or 0), 0)
    except (TypeError, ValueError, OverflowError):
        methodology = 0
    try:
        measured_runs = max(int(row.get("runs") or 1), 1)
    except (TypeError, ValueError, OverflowError):
        measured_runs = 1
    decode = 1 if row.get("decode_tokens_sec") is not None else 0
    return manual, methodology, measured_runs, decode


def _parse_benchmark_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _artifact_changed_after_benchmark(cfg: Any, row: Mapping[str, Any]) -> bool:
    """Return whether local converted artifacts are newer than this benchmark.

    Benchmark stores intentionally remain backward compatible and therefore do not require
    a new persisted artifact-fingerprint field. Instead, current converted-model metadata
    provides a conservative invalidation signal: a newer conversion marker or key OpenVINO
    artifact means the measured result predates the files that would execute now.

    Benchmark timestamps historically have one-second precision. A small mtime tolerance
    prevents a file written earlier in the same second from being misclassified as newer.
    """

    benchmark_at = _parse_benchmark_time(row.get("created_at") or row.get("timestamp"))
    abs_path = getattr(cfg, "abs_path", None)
    if benchmark_at is None or not callable(abs_path):
        return False
    try:
        model_dir = Path(abs_path(BASE_DIR))
    except Exception:
        return False
    if not model_dir.is_dir():
        return False

    marker = model_dir / ".ovllm-conversion.json"
    try:
        metadata = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        metadata = None
    if isinstance(metadata, dict):
        recorded_at = _parse_benchmark_time(metadata.get("recorded_at"))
        if recorded_at is not None and recorded_at > benchmark_at:
            return True

    benchmark_epoch = benchmark_at.timestamp()
    for filename in _ARTIFACT_IDENTITY_FILES:
        candidate = model_dir / filename
        try:
            stat = candidate.stat()
        except OSError:
            continue
        if not candidate.is_file():
            continue
        if stat.st_mtime > benchmark_epoch + _ARTIFACT_MTIME_TOLERANCE_SECONDS:
            return True
    return False


def _compact_benchmark_evidence(row: Mapping[str, Any]) -> dict[str, Any]:
    """Keep advisor/status evidence small even when Benchmark Lab stores samples."""

    fields = (
        "model_id",
        "source_model",
        "backend",
        "weight_format",
        "requested_device",
        "actual_device",
        "load_time_ms",
        "time_to_first_token_ms",
        "total_latency_ms",
        "tokens_sec",
        "decode_tokens_sec",
        "success",
        "timestamp",
        "created_at",
        "hardware_fingerprint",
        "methodology_version",
        "automatic",
        "synthetic",
        "runs",
        "warmup_runs",
        "stability",
        "score",
    )
    return {field: row.get(field) for field in fields if field in row}


class EvidenceMixin:
    def _benchmark_rows(self) -> list[dict[str, Any]]:
        path = Path(self.settings.benchmark_results_file)
        try:
            mtime_ns = path.stat().st_mtime_ns
        except OSError:
            mtime_ns = -1
        now = time.monotonic()
        if (
            self._benchmark_cache_at
            and now - self._benchmark_cache_at < 2.0
            and mtime_ns == self._benchmark_cache_mtime_ns
        ):
            return self._benchmark_cache

        with self._store_lock:
            try:
                data = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                data = {"schema_version": _BENCHMARK_SCHEMA_VERSION, "runs": []}
        if not isinstance(data, dict):
            data = {"schema_version": _BENCHMARK_SCHEMA_VERSION, "runs": []}
        schema = data.get("schema_version", _BENCHMARK_SCHEMA_VERSION)
        if (
            isinstance(schema, bool)
            or not isinstance(schema, int)
            or schema != _BENCHMARK_SCHEMA_VERSION
            or not isinstance(data.get("runs"), list)
        ):
            data = {"schema_version": _BENCHMARK_SCHEMA_VERSION, "runs": []}

        rows: list[dict[str, Any]] = []
        for run in data["runs"]:
            if not isinstance(run, dict):
                continue
            results = run.get("results")
            if not isinstance(results, list):
                continue
            synthetic_run = run.get("mock") is True or run.get("synthetic") is True
            for result in results:
                if not isinstance(result, dict) or result.get("success") is not True:
                    continue
                row = dict(result)
                row["automatic"] = run.get("automatic") is True
                row["synthetic"] = synthetic_run or result.get("synthetic") is True
                row["hardware_fingerprint"] = run.get("hardware_fingerprint")
                row["methodology_version"] = run.get("methodology_version")
                row["created_at"] = run.get("created_at") or result.get("timestamp")
                rows.append(row)
        self._benchmark_cache = rows
        self._benchmark_cache_at = now
        self._benchmark_cache_mtime_ns = mtime_ns
        return rows

    def _latest_benchmark(
        self,
        model_id: str,
        device: str | None = None,
    ) -> dict[str, Any] | None:
        fingerprint = self.hardware_snapshot().get("fingerprint")
        cfg = self.catalog.get(model_id)
        matches = []
        for row in self._benchmark_rows():
            if row.get("model_id") != model_id:
                continue
            if row.get("synthetic") is True:
                continue
            if device and not benchmark_matches_direct_device(row, device):
                continue
            if cfg is None or any(
                row.get(field) != getattr(cfg, field)
                for field in ("source_model", "backend", "weight_format")
            ):
                continue
            row_fingerprint = row.get("hardware_fingerprint")
            if not fingerprint or row_fingerprint != fingerprint:
                continue
            if _artifact_changed_after_benchmark(cfg, row):
                continue
            matches.append(row)
        if not matches:
            return None
        _, selected = max(
            enumerate(matches),
            key=lambda item: (_benchmark_evidence_rank(item[1]), item[0]),
        )
        return _compact_benchmark_evidence(selected)

    def _model_size_key(self, cfg: Any) -> str | None:
        try:
            return str(Path(cfg.abs_path(BASE_DIR)).resolve())
        except Exception:
            return None

    def _actual_converted_size_gb(self, cfg: Any) -> float | None:
        key = self._model_size_key(cfg)
        cached = self._size_cache.get(key) if key else None
        return cached[1] if cached else None

    def measure_converted_size(self, cfg: Any) -> float | None:
        """Measure a converted model off the event loop and cache the result."""

        key = self._model_size_key(cfg)
        if not key:
            return None
        path = Path(key)
        value = dir_size_gb(path) if path.is_dir() else 0.0
        measured = value if value > 0 else None
        self._size_cache[key] = (time.monotonic(), measured)
        return measured

    def forget_model_size(self, cfg: Any) -> None:
        key = self._model_size_key(cfg)
        if key:
            self._size_cache.pop(key, None)
