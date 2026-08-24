from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import BASE_DIR, Settings
from app.server import create_app
from runtime.benchmark_runner import _decode_tokens_sec, _metric_stats


MODELS = ["tinyllama-1.1b-chat-fp16", "tinyllama-1.1b-chat-int4"]


def _client(tmp_path):
    settings = Settings(
        host="127.0.0.1",
        port=8000,
        device="CPU",
        models_file=BASE_DIR / "models.json",
        models_dir=BASE_DIR / "models" / "openvino",
        default_model=None,
        force_mock=True,
        benchmark_results_file=tmp_path / "benchmarks.json",
    )
    return TestClient(create_app(settings))


def test_benchmark_lab_multi_model_run_keeps_samples_and_robust_statistics(tmp_path):
    with _client(tmp_path) as client:
        response = client.post(
            "/v1/benchmarks/run",
            json={
                "models": MODELS,
                "devices": ["CPU"],
                "prompt": "Return one short sentence.",
                "runs": 3,
                "max_tokens": 32,
            },
        )
        assert response.status_code == 200, response.text
        run = response.json()

    assert run["benchmark_schema_version"] == 2
    assert run["methodology_version"] == 2
    assert run["preset"] == "quick"
    assert run["warmup_runs"] == 1
    assert run["runs_per_combo"] == 3
    assert run["synthetic"] is True
    assert run["models"] == MODELS
    assert run["devices"] == ["CPU"]
    assert len(run["results"]) == 2
    assert run["leaders"]["best_balanced"] is not None

    for row in run["results"]:
        assert row["success"] is True
        assert row["synthetic"] is True
        assert row["requested_device"] == "CPU"
        assert row["actual_device"] == "CPU"
        assert row["warmup_runs"] == 1
        assert row["runs"] == 3
        assert len(row["samples"]) == 3
        assert row["statistics"]["total_latency_ms"]["sample_count"] == 3
        assert row["statistics"]["tokens_sec"]["sample_count"] == 3
        assert row["time_to_first_token_ms"] is not None
        assert row["total_latency_ms"] >= row["time_to_first_token_ms"]
        assert row["peak_process_ram_mb"] is None or row["peak_process_ram_mb"] > 0
        assert row["prefill_tokens_sec"] is None


def test_benchmark_lab_history_round_trip_and_mock_evidence_is_not_advisor_evidence(tmp_path):
    with _client(tmp_path) as client:
        run = client.post(
            "/v1/benchmarks/run",
            json={"model": MODELS[0], "devices": ["CPU"], "runs": 3, "max_tokens": 32},
        ).json()
        latest = client.get("/v1/benchmarks/latest").json()["run"]
        listed = client.get("/v1/benchmarks").json()["data"]

    assert latest["run_id"] == run["run_id"]
    assert listed[0]["run_id"] == run["run_id"]
    assert listed[0]["synthetic"] is True


def test_decode_throughput_excludes_ttft_and_first_output_token():
    assert _decode_tokens_sec(5, 1.0, 0.2) == 5.0
    assert _decode_tokens_sec(1, 1.0, 0.2) is None
    assert _decode_tokens_sec(5, 0.2, 0.2) is None


def test_metric_statistics_use_median_and_population_cv():
    stats = _metric_stats([8.0, 10.0, 12.0])
    assert stats is not None
    assert stats["median"] == 10.0
    assert stats["min"] == 8.0
    assert stats["max"] == 12.0
    assert stats["sample_count"] == 3
    assert stats["stddev"] > 0
    assert stats["cv_percent"] > 0


def test_bundled_ui_contains_integrated_benchmark_lab(tmp_path):
    with _client(tmp_path) as client:
        html = client.get("/").text

    assert "Benchmark Lab" in html
    assert "benchmark-run-lab-btn" in html
    assert "benchmark-copy-results" in html
    assert "benchmark-download-json" in html
    assert "Synthetic / mock mode" in html
