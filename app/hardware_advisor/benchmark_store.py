"""Automatic benchmark persistence and freshness checks."""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .common import AUTO_BENCHMARK_TTL_SECONDS, base_device

_BENCHMARK_SCHEMA_VERSION = 1
_READ_ONLY_MARKER = "_inferbridge_read_only"


class AdvisorBenchmarkStoreMixin:
    def _read_store(self) -> dict[str, Any]:
        path = Path(self.settings.benchmark_results_file)
        empty = {"schema_version": _BENCHMARK_SCHEMA_VERSION, "runs": []}
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return empty
        if not isinstance(data, dict) or not isinstance(data.get("runs"), list):
            return empty
        schema = data.get("schema_version", _BENCHMARK_SCHEMA_VERSION)
        if (
            isinstance(schema, bool)
            or not isinstance(schema, int)
            or schema != _BENCHMARK_SCHEMA_VERSION
        ):
            # Evaluation may continue without historical evidence, but append paths must
            # know not to rewrite malformed or future benchmark data with this schema.
            return {**empty, _READ_ONLY_MARKER: True}
        return {"schema_version": schema, "runs": data["runs"]}

    def _append_run(self, run: dict[str, Any]) -> None:
        path = Path(self.settings.benchmark_results_file)
        with self._store_lock:
            data = self._read_store()
            if data.get(_READ_ONLY_MARKER) is True:
                return
            data["runs"] = [item for item in data["runs"] if isinstance(item, dict)]
            data["runs"].append(run)
            data["runs"] = data["runs"][-100:]
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(path.suffix + ".tmp")
            temp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            temp.replace(path)
            self._benchmark_cache_at = 0.0
            self._benchmark_cache_mtime_ns = -1

    def _recent_auto_benchmark_exists(self, model_id: str, device: str) -> bool:
        fingerprint = self.hardware_snapshot().get("fingerprint")
        cutoff = time.time() - AUTO_BENCHMARK_TTL_SECONDS
        with self._store_lock:
            runs = list(self._read_store().get("runs", []))
        for run in reversed(runs):
            if not isinstance(run, dict) or not run.get("automatic"):
                continue
            if run.get("hardware_fingerprint") != fingerprint:
                continue
            created = str(run.get("created_at") or "")
            try:
                timestamp = datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp()
            except (TypeError, ValueError, OverflowError, OSError):
                timestamp = 0.0
            if timestamp < cutoff:
                return False
            results = run.get("results")
            if not isinstance(results, list):
                continue
            for result in results:
                if (
                    isinstance(result, dict)
                    and result.get("model_id") == model_id
                    and base_device(result.get("requested_device")) == base_device(device)
                    and result.get("success") is True
                ):
                    return True
        return False
