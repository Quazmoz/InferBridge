from __future__ import annotations

import asyncio

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
