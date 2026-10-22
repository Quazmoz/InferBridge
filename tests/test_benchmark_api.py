from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from fastapi.testclient import TestClient

from app.config import BASE_DIR, Settings
from app.model_manager import ModelManager
from app.server import create_app
from runtime import benchmark_runner_core
from runtime.benchmark_runner import certify_context_depth, score_benchmark_results
from runtime.openvino_engine import MockEngine

MODEL_ID = "tinyllama-1.1b-chat-fp16"


def make_client(tmp_path, *, api_key: str | None = None):
    settings = Settings(
        host="127.0.0.1",
        port=8000,
        device="CPU",
        models_file=BASE_DIR / "models.json",
        models_dir=BASE_DIR / "models" / "openvino",
        default_model=None,
        api_key=api_key,
        force_mock=True,
        benchmark_results_file=tmp_path / "benchmarks.json",
    )
    return TestClient(create_app(settings))


def test_benchmark_run_persists_latest_and_clear(tmp_path, monkeypatch):
    monkeypatch.setattr(MockEngine, "_reply", lambda self, prompt: "benchmark ok")

    with make_client(tmp_path) as client:
        resp = client.post(
            "/v1/benchmarks/run",
            json={
                "model": MODEL_ID,
                "devices": ["CPU", "AUTO"],
                "prompt": "Say ok.",
                "max_tokens": 8,
                "runs": 1,
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["mock"] is True
        assert body["recommendation"]["model_id"] == MODEL_ID
        assert "not a general speed guarantee" in body["recommendation"]["caveat"]

        results = body["results"]
        assert len(results) == 2
        assert {row["requested_device"] for row in results} == {"CPU", "AUTO"}
        for row in results:
            assert row["model_id"] == MODEL_ID
            assert row["success"] is True
            if row["requested_device"] == "CPU":
                assert row["actual_device"] == "CPU"
            else:
                assert row["actual_device"] in (None, row["requested_device"])
            assert row["load_time_ms"] >= 0
            assert row["time_to_first_token_ms"] is not None
            assert row["total_latency_ms"] >= row["time_to_first_token_ms"]
            assert row["prompt_tokens"] > 0
            assert row["completion_tokens"] > 0
            assert row["tokens_sec"] > 0
            assert row["timestamp"]

        latest = client.get("/v1/benchmarks/latest")
        assert latest.status_code == 200
        assert latest.json()["run"]["run_id"] == body["run_id"]

        listed = client.get("/v1/benchmarks")
        assert listed.status_code == 200
        assert listed.json()["data"][0]["run_id"] == body["run_id"]

        cleared = client.delete("/v1/benchmarks")
        assert cleared.status_code == 200
        assert cleared.json()["deleted_runs"] == 1
        assert client.get("/v1/benchmarks/latest").json()["run"] is None


def test_benchmark_client_run_id_survives_history(tmp_path):
    client_run_id = "c" * 32
    with make_client(tmp_path) as client:
        response = client.post(
            "/v1/benchmarks/run",
            json={
                "model": MODEL_ID, "devices": ["CPU"], "runs": 3,
                "max_tokens": 32, "client_run_id": client_run_id,
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["client_run_id"] == client_run_id
        history = client.get("/v1/benchmarks").json()["data"]
        assert history[0]["client_run_id"] == client_run_id
        events = client.get("/v1/system/status").json()["events"]
        assert any(
            f"Benchmark Lab · run {client_run_id} · combination" in event.get("message", "")
            for event in events
        )


def test_busy_benchmark_returns_conflict(tmp_path, monkeypatch):
    async def busy(*_args, **_kwargs):
        raise benchmark_runner_core.BenchmarkBusyError("A benchmark is already running.")

    monkeypatch.setattr("app.server.run_benchmark_suite", busy)
    with make_client(tmp_path) as client:
        response = client.post(
            "/v1/benchmarks/run",
            json={"model": MODEL_ID, "devices": ["CPU"]},
        )
    assert response.status_code == 409
    assert "already running" in response.json()["detail"]


def test_concurrent_api_benchmarks_reject_instead_of_queue(monkeypatch):
    entered = asyncio.Event()
    release = asyncio.Event()

    async def controlled_runner(*_args, **_kwargs):
        entered.set()
        await release.wait()
        return {"ok": True}

    monkeypatch.setattr(benchmark_runner_core, "_run_benchmark_suite_locked", controlled_runner)

    async def scenario():
        manager = SimpleNamespace()
        args = dict(model_ids=["one"], devices=["CPU"], reject_when_busy=True)
        first = asyncio.create_task(benchmark_runner_core.run_benchmark_suite(manager, **args))
        await entered.wait()
        with pytest.raises(benchmark_runner_core.BenchmarkBusyError):
            await benchmark_runner_core.run_benchmark_suite(manager, **args)
        release.set()
        assert await first == {"ok": True}
        assert await benchmark_runner_core.run_benchmark_suite(manager, **args) == {"ok": True}

    asyncio.run(scenario())


def test_benchmark_validation_errors(tmp_path):
    with make_client(tmp_path) as client:
        missing_model = client.post(
            "/v1/benchmarks/run",
            json={"model": "missing-model", "devices": ["CPU"]},
        )
        assert missing_model.status_code == 404

        bad_device = client.post(
            "/v1/benchmarks/run",
            json={"model": MODEL_ID, "devices": ["AUTO:NPU,,CPU"]},
        )
        assert bad_device.status_code == 400
        assert "Supported examples" in bad_device.json()["detail"]


def test_context_depth_certification_records_exact_depth_and_device(tmp_path):
    settings = Settings(
        host="127.0.0.1",
        port=8000,
        device="CPU",
        models_file=BASE_DIR / "models.json",
        models_dir=BASE_DIR / "models" / "openvino",
        default_model=None,
        api_key=None,
        force_mock=True,
        benchmark_results_file=tmp_path / "benchmarks.json",
    )
    result = asyncio.run(
        certify_context_depth(
            ModelManager(settings),
            model_id=MODEL_ID,
            device="CPU",
            requested_context=64,
        )
    )

    assert result.passed is True
    assert result.requested_device == "CPU"
    assert result.actual_device == "CPU"
    assert result.requested_context == 64
    assert result.prompt_tokens == 64
    assert result.tokens_generated > 0
    assert result.configured_max_context == 2048
    assert result.reserved_output_tokens == 512
    assert result.beyond_requested_context == 1537
    assert result.beyond_rejected is True
    assert not hasattr(result, "tokens_sec")


def test_benchmark_routes_are_api_key_protected(tmp_path):
    with make_client(tmp_path, api_key="sk-secret") as client:
        assert client.get("/v1/benchmarks").status_code == 401
        assert (
            client.get(
                "/v1/benchmarks",
                headers={"Authorization": "Bearer sk-secret"},
            ).status_code
            == 200
        )


def test_scoring_prefers_successful_balanced_result():
    results = [
        {
            "model_id": "model-a",
            "requested_device": "CPU",
            "actual_device": "CPU",
            "success": True,
            "tokens_sec": 10.0,
            "time_to_first_token_ms": 90.0,
            "total_latency_ms": 900.0,
            "load_time_ms": 1000.0,
        },
        {
            "model_id": "model-a",
            "requested_device": "GPU",
            "actual_device": None,
            "success": False,
            "tokens_sec": 1000.0,
            "time_to_first_token_ms": 1.0,
            "total_latency_ms": 10.0,
            "load_time_ms": 10.0,
            "error": "failed",
        },
        {
            "model_id": "model-b",
            "requested_device": "NPU",
            "actual_device": "NPU",
            "success": True,
            "tokens_sec": 20.0,
            "time_to_first_token_ms": 240.0,
            "total_latency_ms": 900.0,
            "load_time_ms": 60_000.0,
        },
    ]

    rec = score_benchmark_results(results)

    assert rec["model_id"] == "model-a"
    assert rec["requested_device"] == "CPU"
    assert results[1]["score"] < 0
    assert results[0]["score"] > results[2]["score"]


def test_index_contains_benchmark_panel(tmp_path):
    with make_client(tmp_path) as client:
        body = client.get("/").text
    assert 'id="benchmark-devices"' in body
    assert 'id="run-benchmark-btn"' in body
    assert 'id="benchmark-recommendation"' in body
    assert "/v1/benchmarks/run" in body
