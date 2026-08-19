from __future__ import annotations

import json
import threading
from types import SimpleNamespace

from app.hardware_advisor.benchmark_store import AdvisorBenchmarkStoreMixin


class _Store(AdvisorBenchmarkStoreMixin):
    def __init__(self, path) -> None:
        self.settings = SimpleNamespace(benchmark_results_file=path)
        self._store_lock = threading.RLock()
        self._benchmark_cache_at = 1.0
        self._benchmark_cache_mtime_ns = 1

    def hardware_snapshot(self):
        return {"fingerprint": "fp"}


def test_nonfinite_schema_does_not_break_advisor_or_get_rewritten(tmp_path):
    path = tmp_path / "benchmarks.json"
    original = '{"schema_version":Infinity,"runs":[{"run_id":"future"}]}'
    path.write_text(original, encoding="utf-8")
    store = _Store(path)

    assert store._read_store()["runs"] == []
    store._append_run({"run_id": "new", "results": []})

    assert path.read_text(encoding="utf-8") == original


def test_future_schema_is_read_only_to_older_advisor(tmp_path):
    path = tmp_path / "benchmarks.json"
    original = json.dumps({"schema_version": 2, "runs": [{"run_id": "future", "extra": {"x": 1}}]})
    path.write_text(original, encoding="utf-8")
    store = _Store(path)

    store._append_run({"run_id": "old-writer", "results": []})

    assert path.read_text(encoding="utf-8") == original


def test_known_schema_drops_malformed_rows_before_append(tmp_path):
    path = tmp_path / "benchmarks.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "runs": ["bad", 7, {"run_id": "valid", "results": []}],
            }
        ),
        encoding="utf-8",
    )
    store = _Store(path)

    store._append_run({"run_id": "new", "results": []})

    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert [row["run_id"] for row in persisted["runs"]] == ["valid", "new"]


def test_malformed_result_container_does_not_break_recent_lookup(tmp_path):
    path = tmp_path / "benchmarks.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "runs": [
                    {
                        "automatic": True,
                        "hardware_fingerprint": "fp",
                        "created_at": "999999999999-01-01T00:00:00Z",
                        "results": "not-a-list",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    store = _Store(path)

    assert store._recent_auto_benchmark_exists("model", "CPU") is False
