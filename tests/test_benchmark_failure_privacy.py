from __future__ import annotations

import asyncio
import json

from runtime import benchmark_runner


def test_run_benchmark_suite_sanitizes_failure_details(monkeypatch):
    secret = "hf_" + "b" * 32

    async def raw_suite(*_args, **_kwargs):
        return {
            "run_id": "bench-test",
            "results": [
                {
                    "model_id": "model",
                    "success": False,
                    "error": rf"C:\Users\Private\models\broken {secret}",
                }
            ],
        }

    monkeypatch.setattr(benchmark_runner._core, "run_benchmark_suite", raw_suite)

    result = asyncio.run(benchmark_runner.run_benchmark_suite(object()))
    error = result["results"][0]["error"]

    assert secret not in error
    assert r"C:\Users\Private" not in error
    assert "[redacted]" in error


def test_benchmark_store_sanitizes_failure_before_persistence(tmp_path):
    secret = "hf_" + "c" * 32
    store = benchmark_runner.BenchmarkStore(tmp_path / "benchmarks.json")
    original = {
        "run_id": "bench-test",
        "results": [
            {
                "model_id": "model",
                "success": False,
                "error": rf"C:\Users\Private\cache\model {secret}",
            }
        ],
    }

    store.append(original)
    stored_error = store.list_runs()[0]["results"][0]["error"]

    assert secret not in stored_error
    assert r"C:\Users\Private" not in stored_error
    assert "[redacted]" in stored_error
    assert secret in original["results"][0]["error"]


def test_benchmark_store_scrubs_historical_failure_rows_on_open(tmp_path):
    secret = "hf_" + "d" * 32
    path = tmp_path / "benchmarks.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "runs": [
                    {
                        "run_id": "old-run",
                        "results": [
                            {
                                "model_id": "model",
                                "success": False,
                                "error": rf"C:\Users\Private\old\model {secret}",
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    store = benchmark_runner.BenchmarkStore(path)
    stored_error = store.list_runs()[0]["results"][0]["error"]
    raw_file = path.read_text(encoding="utf-8")

    assert secret not in stored_error
    assert r"C:\Users\Private" not in stored_error
    assert secret not in raw_file
    assert "[redacted]" in raw_file


def test_nonfinite_benchmark_schema_does_not_break_store_initialization(tmp_path):
    path = tmp_path / "benchmarks.json"
    original = '{"schema_version":Infinity,"runs":[{"run_id":"unsafe"}]}'
    path.write_text(original, encoding="utf-8")

    store = benchmark_runner.BenchmarkStore(path)

    assert store.list_runs() == []
    assert path.read_text(encoding="utf-8") == original


def test_boolean_benchmark_schema_is_not_coerced_to_version_one(tmp_path):
    path = tmp_path / "benchmarks.json"
    original = '{"schema_version":true,"runs":[{"run_id":"unsafe"}]}'
    path.write_text(original, encoding="utf-8")

    store = benchmark_runner.BenchmarkStore(path)

    assert store.list_runs() == []
    assert path.read_text(encoding="utf-8") == original


def test_newer_benchmark_schema_is_left_untouched(tmp_path):
    path = tmp_path / "benchmarks.json"
    original = json.dumps(
        {
            "schema_version": 2,
            "runs": [{"run_id": "future", "new_field": {"value": 1}}],
        }
    )
    path.write_text(original, encoding="utf-8")

    store = benchmark_runner.BenchmarkStore(path)

    assert store.list_runs() == []
    assert path.read_text(encoding="utf-8") == original


def test_malformed_benchmark_rows_are_removed_from_known_schema(tmp_path):
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

    store = benchmark_runner.BenchmarkStore(path)

    assert store.list_runs() == [{"run_id": "valid", "results": []}]
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["runs"] == [{"run_id": "valid", "results": []}]
